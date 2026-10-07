"""Input identity and atomic publication for cropped registered stacks.

功能概述:
    对裁剪输入生成稳定摘要，并在同一文件系统内原子发布或强制替换完整目录。

函数说明:
    ``crop_aoi_digest`` 计算原始 KML 摘要；``crop_input_signature`` 计算配置和输入摘要；
    ``atomic_publish_crop`` 发布 staging 目录且在失败时恢复旧结果。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

from .plan import GammaRadarCropPlan


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def crop_input_signature(plan: GammaRadarCropPlan) -> str:
    """Return a SHA-256 identity for every input that can alter the crop."""

    inputs = {
        "buffer_km": plan.config.buffer_km,
        "kml": _sha256(plan.config.kml_file),
        "lookup_table": _sha256(plan.lookup_table),
        "map_parameter": _sha256(plan.map_grid.parameter_path),
        "min_buffer_coverage": plan.config.min_buffer_coverage,
        "mli_parameter": _sha256(plan.mli_grid.parameter_path),
        "polarizations": list(plan.selected_polarizations),
        "registered_stack": _sha256(plan.config.registered_stack),
        "require_full_aoi_coverage": plan.config.require_full_aoi_coverage,
    }
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def crop_aoi_digest(plan: GammaRadarCropPlan) -> str:
    """Return the SHA-256 identity of the source KML used for one crop."""

    return _sha256(plan.config.kml_file)


def atomic_publish_crop(staging: Path, output: Path, *, force: bool = False) -> None:
    """Atomically publish one complete directory, restoring an old target on failure."""

    source = Path(staging).resolve(strict=True)
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.parent != destination.parent:
        raise ValueError("crop staging and output must have the same parent directory")
    if destination.exists() and not force:
        raise FileExistsError(f"crop output appeared during execution: {destination}")
    backup: Path | None = None
    if destination.exists():
        backup = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.backup"
        os.replace(destination, backup)
    try:
        os.replace(source, destination)
    except BaseException:
        if backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    if backup is not None:
        shutil.rmtree(backup)


__all__ = ["atomic_publish_crop", "crop_aoi_digest", "crop_input_signature"]
