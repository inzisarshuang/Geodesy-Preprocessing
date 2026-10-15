"""Transactional batch execution for GAMMA registered-stack cropping.

功能概述:
    用一个窗口逐日期、逐极化调用 SLC_copy，验证产品、生成快视图和清单，并原子发布。

函数说明:
    ``CropRunSummary`` 汇总运行结果；``run_crop_stack`` 执行或跳过完整裁剪栈。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.case_layout import resolve_case_log_path
from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.sar.common.models import (
    RegisteredStackAsset,
    RegisteredStackManifest,
)
from geodesy_preprocessing.sar.common.stack_manifest import (
    read_stack_manifest,
    write_stack_manifest,
)
from geodesy_preprocessing.sar.inventory.common.images import write_images_inventory

from .gamma import GammaCommandError, GammaCommandRunner
from .masking import CropGeometryResult, build_crop_geometry
from .parameters import read_gamma_radar_grid
from .plan import GammaRadarCropPlan
from .publication import atomic_publish_crop, crop_aoi_digest, crop_input_signature


_TASK = "PRE_GAMMA_SAR_RSLC_CROP"


@dataclass(frozen=True)
class CropRunSummary:
    """Asset counts and final output for one concluded crop batch."""

    total: int
    completed: int
    skipped: int
    failed: int
    output_dir: Path


class _LoggedRunner:
    def __init__(self, delegate, stdout, stderr, commands: list[tuple[str, ...]]) -> None:
        self.delegate = delegate
        self.stdout = stdout
        self.stderr = stderr
        self.commands = commands

    def run(self, command):
        normalized = tuple(str(value) for value in command)
        self.commands.append(normalized)
        self.stdout.write("$ " + " ".join(normalized) + "\n")
        self.stdout.flush()
        try:
            result = self.delegate.run(normalized)
        except GammaCommandError as error:
            self.stdout.write(error.result.stdout)
            self.stderr.write(error.result.stderr)
            self.stdout.flush()
            self.stderr.flush()
            raise
        if result is not None:
            self.stdout.write(getattr(result, "stdout", ""))
            self.stderr.write(getattr(result, "stderr", ""))
        self.stdout.flush()
        self.stderr.flush()
        return result


def _event(sink: Callable[[str], None], **values) -> None:
    sink(emit_progress(task=_TASK, **values))


def _source_snapshot(plan: GammaRadarCropPlan) -> dict[Path, tuple[int, int]]:
    paths = {
        plan.source_root / path
        for asset in plan.assets
        for path in (asset.data_path, asset.parameter_path)
    }
    return {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in paths}


def _validate_source_snapshot(snapshot: dict[Path, tuple[int, int]]) -> None:
    changed = [
        str(path)
        for path, expected in snapshot.items()
        if not path.exists() or (path.stat().st_size, path.stat().st_mtime_ns) != expected
    ]
    if changed:
        raise RuntimeError(f"source registered stack changed during crop: {', '.join(changed)}")


def _existing_matches(plan: GammaRadarCropPlan, signature: str) -> bool:
    provenance_path = plan.output_dir / "crop_window.json"
    if not provenance_path.is_file():
        return False
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if provenance.get("input_signature") != signature:
            return False
        read_stack_manifest(plan.output_dir / "registered_stack.json")
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False
    return True


def _output_stem(asset: RegisteredStackAsset, polarization_count: int) -> str:
    return asset.date if polarization_count == 1 else f"{asset.date}.{asset.polarization}"


def _move_geometry_artifacts(geometry: CropGeometryResult, staging: Path) -> None:
    for path in (
        geometry.buffered_geojson,
        geometry.map_mask_tif,
        geometry.radar_mask_bmp,
    ):
        path.replace(staging / path.name)


def run_crop_stack(
    plan: GammaRadarCropPlan,
    *,
    force: bool = False,
    command_runner=None,
    geometry_builder=build_crop_geometry,
    progress_sink: Callable[[str], None] = print,
) -> CropRunSummary:
    """Crop every selected registered asset and atomically publish one complete stack."""

    if not isinstance(plan, GammaRadarCropPlan):
        raise TypeError("plan must be GammaRadarCropPlan")
    total = len(plan.assets)
    signature = crop_input_signature(plan)
    lock = plan.output_dir.parent / f".{plan.output_dir.name}.lock"
    plan.output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise FileExistsError(f"another crop is using target: {plan.output_dir}") from error
    os.close(descriptor)
    staging: Path | None = None
    current_item: str | None = None
    current_index: int | None = None
    try:
        if plan.output_dir.exists() and _existing_matches(plan, signature) and not force:
            _event(progress_sink, event="batch_finished", status="skipped", total=total)
            return CropRunSummary(total, 0, total, 0, plan.output_dir)
        if plan.output_dir.exists() and not force:
            raise FileExistsError(
                f"crop output was produced from different inputs: {plan.output_dir}"
            )
        snapshot = _source_snapshot(plan)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{plan.output_dir.name}.",
                suffix=".stage",
                dir=plan.output_dir.parent,
            )
        )
        log_dir = (
            resolve_case_log_path(
                plan.config.case_root, "gamma_sar_rslc_crop", plan.config.name
            )
            if plan.config.case_root is not None
            else plan.config.project_root
            / "LOG/PRE/GAMMA/gamma_sar_rslc_crop"
            / plan.config.name
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        commands: list[tuple[str, ...]] = []
        delegate = command_runner or GammaCommandRunner()
        assets_out: list[RegisteredStackAsset] = []
        _event(progress_sink, event="batch_started", status="start", total=total)
        with (log_dir / "stdout.log").open("w", encoding="utf-8") as stdout, (
            log_dir / "stderr.log"
        ).open("w", encoding="utf-8") as stderr:
            logged = _LoggedRunner(delegate, stdout, stderr, commands)
            geometry = geometry_builder(plan, staging / "geometry_work", runner=logged)
            (staging / "RSLC").mkdir()
            (staging / "quicklooks").mkdir()
            for index, (asset, source_grid) in enumerate(
                zip(plan.assets, plan.asset_grids, strict=True), start=1
            ):
                item = f"{asset.date}:{asset.polarization}"
                current_item = item
                current_index = index
                _event(
                    progress_sink,
                    event="item_started",
                    status="start",
                    item=item,
                    index=index,
                    total=total,
                    stage="SLC_copy",
                )
                stem = _output_stem(asset, len(plan.selected_polarizations))
                relative_data = Path("RSLC") / f"{stem}.rslc"
                relative_parameter = Path("RSLC") / f"{stem}.rslc.par"
                output_data = staging / relative_data
                output_parameter = staging / relative_parameter
                fcase = "1" if source_grid.image_format == "FCOMPLEX" else "4"
                logged.run(
                    (
                        "SLC_copy",
                        str(plan.source_root / asset.data_path),
                        str(plan.source_root / asset.parameter_path),
                        str(output_data),
                        str(output_parameter),
                        fcase,
                        "1.0",
                        str(geometry.window.roff),
                        str(geometry.window.nr),
                        str(geometry.window.loff),
                        str(geometry.window.nl),
                    )
                )
                if not output_data.is_file() or output_data.stat().st_size == 0:
                    raise ValueError(f"SLC_copy did not create data: {output_data}")
                output_grid = read_gamma_radar_grid(output_parameter)
                if (output_grid.width, output_grid.nlines) != (
                    geometry.window.nr,
                    geometry.window.nl,
                ):
                    raise ValueError(f"cropped RSLC dimensions are wrong: {output_parameter}")
                if output_grid.image_format != source_grid.image_format:
                    raise ValueError(f"cropped RSLC format changed: {output_parameter}")
                quicklook = staging / "quicklooks" / f"{stem}.rslc.bmp"
                logged.run(
                    (
                        "rasSLC",
                        str(output_data),
                        str(output_parameter),
                        "-",
                        "0",
                        str(plan.mli_grid.range_looks),
                        str(plan.mli_grid.azimuth_looks),
                        "1.0",
                        "0.35",
                        str(quicklook),
                    )
                )
                if not quicklook.is_file() or quicklook.stat().st_size == 0:
                    raise ValueError(f"rasSLC did not create quicklook: {quicklook}")
                assets_out.append(
                    RegisteredStackAsset(
                        asset.date,
                        asset.polarization,
                        relative_data,
                        relative_parameter,
                    )
                )
                _event(
                    progress_sink,
                    event="item_finished",
                    status="complete",
                    item=item,
                    index=index,
                    total=total,
                    stage="publish",
                )
                current_item = None
                current_index = None
        _move_geometry_artifacts(geometry, staging)
        shutil.rmtree(staging / "geometry_work")
        provenance = {
            "aoi_sha256": crop_aoi_digest(plan),
            "buffer_km": plan.config.buffer_km,
            "commands": [list(command) for command in commands],
            "coverage": {
                "buffered": geometry.coverage.buffered_ratio,
                "original": geometry.coverage.original_ratio,
            },
            "input_signature": signature,
            "polarizations": list(plan.selected_polarizations),
            "source_manifest": str(plan.config.registered_stack),
            "window": {
                "loff": geometry.window.loff,
                "nl": geometry.window.nl,
                "nr": geometry.window.nr,
                "roff": geometry.window.roff,
                "warnings": list(geometry.window.warnings),
            },
        }
        (staging / "crop_window.json").write_text(
            json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        write_images_inventory(staging / "images", plan.manifest.dates)
        output_manifest = RegisteredStackManifest(
            schema_version=2,
            status="complete",
            sensor=plan.manifest.sensor,
            source_product_type=plan.manifest.source_product_type,
            backend=plan.manifest.backend,
            geometry_type=plan.manifest.geometry_type,
            reference_date=plan.manifest.reference_date,
            dates=plan.manifest.dates,
            frequencies=plan.manifest.frequencies,
            polarizations=plan.selected_polarizations,
            assets=tuple(assets_out),
            orbit_direction=plan.manifest.orbit_direction,
            track=plan.manifest.track,
            frame=plan.manifest.frame,
            shape=(geometry.window.nl, geometry.window.nr),
            provenance_path=Path("crop_window.json"),
        )
        write_stack_manifest(output_manifest, staging / "registered_stack.json")
        _validate_source_snapshot(snapshot)
        atomic_publish_crop(staging, plan.output_dir, force=force)
        staging = None
        _event(
            progress_sink,
            event="batch_finished",
            status="complete",
            index=total,
            total=total,
            stage="publish",
        )
        return CropRunSummary(total, total, 0, 0, plan.output_dir)
    except BaseException:
        if current_item is not None:
            _event(
                progress_sink,
                event="item_finished",
                status="failed",
                item=current_item,
                index=current_index,
                total=total,
                stage="failed",
            )
        _event(progress_sink, event="batch_finished", status="failed", total=total)
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        lock.unlink(missing_ok=True)


__all__ = ["CropRunSummary", "run_crop_stack"]
