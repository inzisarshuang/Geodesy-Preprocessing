"""NISAR GSLC 输入身份、计划记录和正式目录原子发布。

功能概述:
    为影响栈结果的配置与输入生成稳定摘要，把 plan-only 记录写入项目日志，并只在
    目标不存在时用同文件系统原子改名发布完整目录。

函数说明:
    ``gslc_input_signature`` 计算输入摘要；``publish_gslc_plan`` 发布无凭据计划 JSON；
    ``atomic_publish_gslc`` 原子发布 staging 且绝不自动替换正式结果。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .models import NisarGslcStackPlan


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def gslc_input_signature(plan: NisarGslcStackPlan) -> str:
    """Return a SHA-256 over every input and choice that can change a GSLC stack."""

    if not isinstance(plan, NisarGslcStackPlan):
        raise TypeError("plan must be NisarGslcStackPlan")
    payload = {
        "band": plan.case.band,
        "buffer_km": plan.case.buffer_km,
        "dates": list(plan.dates),
        "frequency": plan.case.frequency,
        "grid": {
            "center_frequency_hz": plan.grid.center_frequency_hz,
            "epsg": plan.grid.epsg,
            "shape": list(plan.grid.shape),
            "x_spacing": plan.grid.x_spacing,
            "x_start": plan.grid.x_start,
            "y_spacing": plan.grid.y_spacing,
            "y_start": plan.grid.y_start,
        },
        "kml_sha256": _sha256(plan.case.kml_file),
        "kml_placemark": plan.case.kml_placemark,
        "polarizations": list(plan.selected_polarizations),
        "product_specification_version": plan.product_specification_version,
        "reference_date": plan.reference_date,
        "source_manifest_sha256": _sha256(plan.case.source_manifest),
        "sources": [
            {
                "date": asset.date,
                "dataset_path": asset.channel.dataset_path,
                "file": _relative(asset.source_file, plan.case.project_root),
                "polarization": asset.polarization,
                "sha256": asset.source_sha256,
            }
            for asset in plan.assets
        ],
        "window": {
            "column_start": plan.window.column_start,
            "column_stop": plan.window.column_stop,
            "row_start": plan.window.row_start,
            "row_stop": plan.window.row_stop,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_payload(plan: NisarGslcStackPlan) -> dict[str, object]:
    return {
        "assets": len(plan.assets),
        "band": plan.case.band,
        "buffer_km": plan.case.buffer_km,
        "buffered_coverage": dict(plan.buffered_coverage),
        "dates": list(plan.dates),
        "frame": plan.frame,
        "frequency": plan.case.frequency,
        "grid": {
            "center_frequency_hz": plan.grid.center_frequency_hz,
            "epsg": plan.grid.epsg,
            "shape": list(plan.grid.shape),
            "x_spacing": plan.grid.x_spacing,
            "x_start": plan.grid.x_start,
            "y_spacing": plan.grid.y_spacing,
            "y_start": plan.grid.y_start,
        },
        "input_signature": gslc_input_signature(plan),
        "orbit_direction": plan.orbit_direction,
        "output_dir": _relative(plan.case.output_dir, plan.case.project_root),
        "polarizations": list(plan.selected_polarizations),
        "reference_date": plan.reference_date,
        "roi_coverage": dict(plan.roi_coverage),
        "status": "planned",
        "track": plan.track,
        "window": {
            "column_start": plan.window.column_start,
            "column_stop": plan.window.column_stop,
            "row_start": plan.window.row_start,
            "row_stop": plan.window.row_stop,
            "shape": list(plan.window.shape),
        },
    }


def publish_gslc_plan(plan: NisarGslcStackPlan) -> Path:
    """Atomically write one plan-only JSON under the project's PRE log tree."""

    destination = (
        plan.case.project_root / "LOG/PRE/NISAR/GSLC" / plan.case.name / "plan.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(_plan_payload(plan), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def atomic_publish_gslc(staging: str | Path, output: str | Path) -> None:
    """Publish one complete staging directory without replacing an existing result."""

    source = Path(staging).resolve(strict=True)
    destination = Path(output).resolve()
    if not source.is_dir():
        raise ValueError(f"GSLC staging must be a directory: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.parent != destination.parent:
        raise ValueError("GSLC staging and output must have the same parent directory")
    if destination.exists():
        raise FileExistsError(f"GSLC stack output already exists: {destination}")
    os.replace(source, destination)


__all__ = ["atomic_publish_gslc", "gslc_input_signature", "publish_gslc_plan"]
