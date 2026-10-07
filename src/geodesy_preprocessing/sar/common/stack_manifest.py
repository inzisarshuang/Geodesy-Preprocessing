"""Transactional JSON publication for registered-stack manifests.

功能概述:
    校验清单引用的项目内文件，并以临时文件替换方式发布确定性 JSON。

函数说明:
    ``write_stack_manifest`` 原子写入完整清单；``read_stack_manifest``
    读取并重新校验清单及其引用文件。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from geodesy_preprocessing.sar.common.models import (
    BackendIdentity,
    GeometryType,
    RegisteredStackAsset,
    RegisteredStackManifest,
)


def _checked_relative_path(value: Path, root: Path, field: str) -> Path:
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"{field} path must stay inside the manifest directory: {value}")
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{field} path escapes the manifest directory: {value}") from error
    if not resolved.exists():
        raise FileNotFoundError(f"referenced {field} data does not exist: {value}")
    return value


def _validate_references(manifest: RegisteredStackManifest, root: Path) -> None:
    for asset in manifest.assets:
        _checked_relative_path(asset.data_path, root, "asset data")
        _checked_relative_path(asset.parameter_path, root, "asset parameter")
    for field in ("quality_path", "provenance_path"):
        value = getattr(manifest, field)
        if value is not None:
            _checked_relative_path(value, root, field)


def _as_payload(manifest: RegisteredStackManifest) -> dict[str, Any]:
    return {
        "backend": {"name": manifest.backend.name, "version": manifest.backend.version},
        "assets": [
            {
                "data_path": str(asset.data_path),
                "date": asset.date,
                "parameter_path": str(asset.parameter_path),
                "polarization": asset.polarization,
            }
            for asset in manifest.assets
        ],
        "common_extent": manifest.common_extent,
        "coordinate_system": manifest.coordinate_system,
        "dates": list(manifest.dates),
        "frame": manifest.frame,
        "frequencies": list(manifest.frequencies),
        "geometry_type": manifest.geometry_type.value,
        "orbit_direction": manifest.orbit_direction,
        "origin": manifest.origin,
        "pixel_spacing": manifest.pixel_spacing,
        "polarizations": list(manifest.polarizations),
        "provenance_path": (
            str(manifest.provenance_path) if manifest.provenance_path is not None else None
        ),
        "quality_path": str(manifest.quality_path) if manifest.quality_path is not None else None,
        "reference_date": manifest.reference_date,
        "schema_version": manifest.schema_version,
        "sensor": manifest.sensor,
        "shape": manifest.shape,
        "source_product_type": manifest.source_product_type,
        "status": manifest.status,
        "track": manifest.track,
    }


def _from_payload(payload: dict[str, Any]) -> RegisteredStackManifest:
    values = dict(payload)
    backend = values.pop("backend")
    values["backend"] = BackendIdentity(**backend)
    values["geometry_type"] = GeometryType(values["geometry_type"])
    values["assets"] = tuple(
        RegisteredStackAsset(
            date=asset["date"],
            polarization=asset["polarization"],
            data_path=Path(asset["data_path"]),
            parameter_path=Path(asset["parameter_path"]),
        )
        for asset in values["assets"]
    )
    for field in ("dates", "frequencies", "polarizations"):
        values[field] = tuple(values[field])
    for field in ("quality_path", "provenance_path"):
        if values.get(field) is not None:
            values[field] = Path(values[field])
    for field in ("pixel_spacing", "origin", "shape", "common_extent"):
        if values.get(field) is not None:
            values[field] = tuple(values[field])
    return RegisteredStackManifest(**values)


def write_stack_manifest(manifest: RegisteredStackManifest, path: Path) -> Path:
    """Validate references and atomically publish a deterministic JSON manifest."""

    if not isinstance(manifest, RegisteredStackManifest):
        raise TypeError("manifest must be RegisteredStackManifest")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_references(manifest, destination.parent)
    encoded = (
        json.dumps(_as_payload(manifest), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def read_stack_manifest(path: Path) -> RegisteredStackManifest:
    """Read a registered-stack JSON document and validate every referenced path."""

    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stack manifest root must be a JSON object")
    manifest = _from_payload(payload)
    _validate_references(manifest, source.parent)
    return manifest


__all__ = ["read_stack_manifest", "write_stack_manifest"]
