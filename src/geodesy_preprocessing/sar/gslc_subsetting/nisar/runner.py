"""NISAR GSLC 复数栈的事务执行、质量记录、日志和清单发布。

功能概述:
    逐日期和极化生成复数 GeoTIFF、参数 JSON 与快视图；全部成功后一次性发布
    schema-v2 地理网格栈。失败时不发布部分成果，并保留脱敏错误记录。

函数说明:
    ``GslcRunSummary`` 汇总批处理结果；``run_nisar_gslc_stack`` 执行、跳过相同结果
    或拒绝陈旧/不完整结果。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio

from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.sar.common.models import (
    BackendIdentity,
    GeometryType,
    RegisteredStackAsset,
    RegisteredStackManifest,
)
from geodesy_preprocessing.sar.common.stack_manifest import (
    read_stack_manifest,
    write_stack_manifest,
)
from geodesy_preprocessing.sar.inventory.common.images import write_images_inventory

from .crop import write_gslc_complex_geotiff, write_gslc_quicklook
from .models import NisarGslcStackPlan
from .publication import atomic_publish_gslc, gslc_input_signature


_TASK = "PRE_NISAR_GSLC"


@dataclass(frozen=True)
class GslcRunSummary:
    """Asset counts and final output for one concluded GSLC batch."""

    total: int
    completed: int
    skipped: int
    failed: int
    output_dir: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _redact(value: object) -> str:
    text = str(value)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1<redacted>@", text)
    text = re.sub(r"(?i)([?&])[^\s]*", r"\1<redacted>", text)
    text = re.sub(
        r"(?i)\b(password|passwd|token|secret|authorization|api_key)\b(\s*[=:]\s*)[^\s]+",
        r"\1\2<redacted>",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer <redacted>", text)
    return text


def _snapshot(plan: NisarGslcStackPlan) -> dict[Path, tuple[int, int]]:
    paths = {asset.source_file for asset in plan.assets}
    return {
        path: (path.stat().st_size, path.stat().st_mtime_ns) for path in paths
    }


def _validate_snapshot(snapshot: dict[Path, tuple[int, int]]) -> None:
    for path, expected in snapshot.items():
        if not path.is_file():
            raise RuntimeError(f"source GSLC disappeared during execution: {path.name}")
        actual = (path.stat().st_size, path.stat().st_mtime_ns)
        if actual != expected:
            raise RuntimeError(f"source GSLC changed during execution: {path.name}")


def _existing_matches(plan: NisarGslcStackPlan, signature: str) -> bool:
    output = plan.case.output_dir
    if not output.exists():
        return False
    if not output.is_dir():
        raise FileExistsError(f"GSLC stack output conflicts with a file: {output}")
    required = (
        "registered_stack.json",
        "images",
        "crop_window.json",
        "quality.json",
        "provenance.json",
    )
    if not all((output / name).is_file() for name in required):
        raise FileExistsError(f"incomplete GSLC stack output exists: {output}")
    try:
        provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
        read_stack_manifest(output / "registered_stack.json")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise FileExistsError(f"incomplete GSLC stack output exists: {output}") from error
    if provenance.get("input_signature") != signature:
        raise FileExistsError(f"GSLC stack output was produced from different inputs: {output}")
    return True


def _asset_quality(path: Path) -> dict[str, object]:
    invalid = 0
    pixels = 0
    with rasterio.open(path) as dataset:
        for _, window in dataset.block_windows(1):
            mask = dataset.read_masks(1, window=window)
            pixels += int(mask.size)
            invalid += int(np.count_nonzero(mask == 0))
    return {
        "invalid_pixels": invalid,
        "total_pixels": pixels,
        "valid_pixels": pixels - invalid,
    }


def _failure_record(plan: NisarGslcStackPlan, error: BaseException, item: str | None) -> Path:
    plan.case.output_dir.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(
        tempfile.mkdtemp(
            prefix=f"{plan.case.output_dir.name}.failed-logs-",
            dir=plan.case.output_dir.parent,
        )
    )
    _write_json(
        directory / "error.json",
        {
            "error_type": type(error).__name__,
            "item": item,
            "message": _redact(error),
            "status": "failed",
            "task": _TASK,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )
    return directory


def _extent(plan: NisarGslcStackPlan) -> tuple[float, float, float, float]:
    x_center = plan.grid.x_start + plan.window.column_start * plan.grid.x_spacing
    y_center = plan.grid.y_start + plan.window.row_start * plan.grid.y_spacing
    height, width = plan.window.shape
    x0 = x_center - 0.5 * plan.grid.x_spacing
    x1 = x_center + (width - 0.5) * plan.grid.x_spacing
    y0 = y_center - 0.5 * plan.grid.y_spacing
    y1 = y_center + (height - 0.5) * plan.grid.y_spacing
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def run_nisar_gslc_stack(
    plan: NisarGslcStackPlan,
    progress_sink: Callable[[str], None] = print,
) -> GslcRunSummary:
    """Extract and atomically publish every planned date/polarization GSLC asset."""

    if not isinstance(plan, NisarGslcStackPlan):
        raise TypeError("plan must be NisarGslcStackPlan")
    total = len(plan.assets)
    signature = gslc_input_signature(plan)
    output = plan.case.output_dir
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / f".{output.name}.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise FileExistsError(f"another NISAR GSLC run is using target: {output}") from error
    os.close(descriptor)
    staging: Path | None = None
    current_item: str | None = None
    log_dir = plan.case.project_root / "LOG/PRE/NISAR/GSLC" / plan.case.name
    log_dir.mkdir(parents=True, exist_ok=True)
    run_log = log_dir / "run.log"

    def event(**values) -> None:
        line = emit_progress(task=_TASK, **values)
        progress_sink(line)
        with run_log.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    try:
        if _existing_matches(plan, signature):
            event(event="batch_finished", status="skipped", total=total)
            return GslcRunSummary(total, 0, total, 0, output)
        snapshot = _snapshot(plan)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{output.name}.", suffix=".stage", dir=output.parent)
        )
        stack_assets: list[RegisteredStackAsset] = []
        quality_assets: list[dict[str, object]] = []
        event(event="batch_started", status="start", total=total, stage="extract")
        for index, asset in enumerate(plan.assets, start=1):
            current_item = f"{asset.date}:{asset.polarization}"
            event(
                event="item_started",
                status="running",
                item=current_item,
                index=index,
                total=total,
                stage="extract",
            )
            relative_data = (
                Path("complex") / asset.polarization / asset.date / f"{asset.date}.tif"
            )
            relative_parameter = (
                Path("metadata") / asset.polarization / asset.date / f"{asset.date}.json"
            )
            relative_quicklook = (
                Path("quicklooks") / asset.polarization / f"{asset.date}.png"
            )
            data_path = write_gslc_complex_geotiff(asset, staging / relative_data)
            quicklook_path = write_gslc_quicklook(data_path, staging / relative_quicklook)
            transform_values: list[float]
            with rasterio.open(data_path) as dataset:
                transform_values = list(tuple(dataset.transform)[:6])
                shape = [dataset.height, dataset.width]
            output_digest = _sha256(data_path)
            parameter = {
                "band": plan.case.band,
                "center_frequency_hz": asset.grid.center_frequency_hz,
                "dataset_path": asset.channel.dataset_path,
                "date": asset.date,
                "dtype": "complex64",
                "epsg": asset.grid.epsg,
                "frequency": plan.case.frequency,
                "nodata": None,
                "output_sha256": output_digest,
                "polarization": asset.polarization,
                "shape": shape,
                "source_file": str(asset.source_file.relative_to(plan.case.project_root)),
                "source_sha256": asset.source_sha256,
                "transform": transform_values,
            }
            _write_json(staging / relative_parameter, parameter)
            metrics = _asset_quality(data_path)
            quality_assets.append(
                {
                    "data_path": str(relative_data),
                    "date": asset.date,
                    "output_sha256": output_digest,
                    "polarization": asset.polarization,
                    "quicklook_path": str(relative_quicklook),
                    **metrics,
                }
            )
            stack_assets.append(
                RegisteredStackAsset(
                    date=asset.date,
                    polarization=asset.polarization,
                    data_path=relative_data,
                    parameter_path=relative_parameter,
                )
            )
            event(
                event="item_finished",
                status="complete",
                item=current_item,
                index=index,
                total=total,
                stage="extract",
            )
            current_item = None

        crop_window = {
            "buffer_km": plan.case.buffer_km,
            "buffered_coverage": dict(plan.buffered_coverage),
            "input_signature": signature,
            "roi_coverage": dict(plan.roi_coverage),
            "window": {
                "column_start": plan.window.column_start,
                "column_stop": plan.window.column_stop,
                "row_start": plan.window.row_start,
                "row_stop": plan.window.row_stop,
                "shape": list(plan.window.shape),
            },
        }
        _write_json(staging / "crop_window.json", crop_window)
        _write_json(
            staging / "quality.json",
            {"assets": quality_assets, "status": "complete"},
        )
        _write_json(
            staging / "provenance.json",
            {
                "backend": "nisar-native",
                "input_signature": signature,
                "product_specification_version": plan.product_specification_version,
                "source_manifest": str(
                    plan.case.source_manifest.relative_to(plan.case.project_root)
                ),
                "status": "complete",
            },
        )
        write_images_inventory(staging / "images", plan.dates)
        origin = (
            plan.grid.x_start + plan.window.column_start * plan.grid.x_spacing,
            plan.grid.y_start + plan.window.row_start * plan.grid.y_spacing,
        )
        manifest = RegisteredStackManifest(
            schema_version=2,
            status="complete",
            sensor="NISAR",
            source_product_type="GSLC",
            backend=BackendIdentity("nisar-native", plan.product_specification_version),
            geometry_type=GeometryType.GEOCODED_GRID,
            reference_date=plan.reference_date,
            dates=plan.dates,
            frequencies=(f"{plan.case.band}{plan.case.frequency}",),
            polarizations=plan.selected_polarizations,
            assets=tuple(stack_assets),
            orbit_direction=plan.orbit_direction,
            track=str(plan.track),
            frame=str(plan.frame),
            coordinate_system=f"EPSG:{plan.grid.epsg}",
            pixel_spacing=(plan.grid.x_spacing, plan.grid.y_spacing),
            origin=origin,
            shape=plan.window.shape,
            common_extent=_extent(plan),
            quality_path=Path("quality.json"),
            provenance_path=Path("provenance.json"),
        )
        write_stack_manifest(manifest, staging / "registered_stack.json")
        _validate_snapshot(snapshot)
        atomic_publish_gslc(staging, output)
        staging = None
        event(
            event="batch_finished",
            status="complete",
            index=total,
            total=total,
            stage="publish",
        )
        return GslcRunSummary(total, total, 0, 0, output)
    except BaseException as error:
        if current_item is not None:
            event(
                event="item_finished",
                status="failed",
                item=current_item,
                total=total,
                stage="failed",
            )
        event(event="batch_finished", status="failed", total=total, stage="failed")
        _failure_record(plan, error, current_item)
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        lock.unlink(missing_ok=True)


__all__ = ["GslcRunSummary", "run_nisar_gslc_stack"]
