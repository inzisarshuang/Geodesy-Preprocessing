"""NISAR GSLC 输入身份和正式目录原子发布。

功能概述:
    为影响栈结果的配置与输入生成稳定摘要，并用同文件系统原子改名发布完整目录。

函数说明:
    ``gslc_input_signature`` 计算输入摘要；``atomic_publish_gslc`` 原子发布 staging。
"""

from __future__ import annotations

import hashlib
import json
import os
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


def atomic_publish_gslc(staging: str | Path, output: str | Path) -> None:
    """Publish one complete staging directory, replacing an existing result."""

    source = Path(staging).resolve(strict=True)
    destination = Path(output).resolve()
    if not source.is_dir():
        raise ValueError(f"GSLC staging must be a directory: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.parent != destination.parent:
        raise ValueError("GSLC staging and output must have the same parent directory")
    backup = destination.parent / f".{destination.name}.backup"
    if backup.exists():
        raise FileExistsError(f"GSLC backup path already exists: {backup}")
    if destination.exists() or destination.is_symlink():
        os.replace(destination, backup)
    try:
        os.replace(source, destination)
    except BaseException:
        if backup.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        import shutil

        shutil.rmtree(backup)


__all__ = ["atomic_publish_gslc", "gslc_input_signature"]
