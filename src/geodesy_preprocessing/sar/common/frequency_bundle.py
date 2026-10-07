"""两个完整单频注册栈之间的 NISAR 双频关系清单。

功能概述:
    保持 LA/LB 栅格物理独立，只校验共同的采集身份与日期/极化，并使用清单中的
    真实数值中心频率确定高低频角色，以相对路径事务发布 JSON。

函数说明:
    ``DualFrequencyBundle`` 保存关系；``build_dual_frequency_bundle`` 校验两个栈；
    ``write_dual_frequency_bundle`` 与 ``read_dual_frequency_bundle`` 原子写入并重验引用。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .stack_manifest import read_stack_manifest


@dataclass(frozen=True)
class DualFrequencyBundle:
    """Immutable relationship between two complete single-frequency stacks."""

    schema_version: int
    main_manifest: Path
    auxiliary_manifest: Path
    main_frequency_hz: float
    auxiliary_frequency_hz: float
    low_frequency_role: str
    high_frequency_role: str
    sensor: str
    track: str | None
    frame: str | None
    orbit_direction: str | None
    reference_date: str
    dates: tuple[str, ...]
    polarizations: tuple[str, ...]
    aoi_digest: str
    buffer_km: float


def _frequency(manifest, label: str) -> float:
    if len(manifest.frequencies) != 1:
        raise ValueError(f"{label} stack must contain exactly one frequency")
    try:
        value = float(manifest.frequencies[0])
    except ValueError as error:
        raise ValueError(f"{label} stack frequency must be numeric") from error
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{label} stack frequency must be positive")
    return value


def _crop_identity(manifest, manifest_path: Path, label: str) -> tuple[str, float]:
    provenance_path = manifest.provenance_path
    if provenance_path is None:
        raise ValueError(f"{label} stack has no crop provenance")
    source = (manifest_path.parent / provenance_path).resolve()
    if not source.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError(f"{label} crop provenance escapes its stack")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
        digest = str(document["aoi_sha256"])
        buffer_km = float(document["buffer_km"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} stack has invalid crop provenance") from error
    if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
        raise ValueError(f"{label} crop provenance has invalid AOI digest")
    if not math.isfinite(buffer_km) or buffer_km < 0:
        raise ValueError(f"{label} crop provenance has invalid buffer")
    return digest, buffer_km


def build_dual_frequency_bundle(
    main_manifest: str | Path,
    auxiliary_manifest: str | Path,
    aoi_digest: str,
    buffer_km: float,
) -> DualFrequencyBundle:
    """Read and validate two complete schema-v2 single-frequency manifests."""

    main_path = Path(main_manifest).expanduser().resolve(strict=True)
    auxiliary_path = Path(auxiliary_manifest).expanduser().resolve(strict=True)
    if main_path == auxiliary_path:
        raise ValueError("main and auxiliary stack manifests must be different")
    main = read_stack_manifest(main_path)
    auxiliary = read_stack_manifest(auxiliary_path)
    if main.sensor.lower() != "nisar" or auxiliary.sensor.lower() != "nisar":
        raise ValueError("sensor must be nisar for a NISAR dual-frequency bundle")
    for field in ("track", "frame", "orbit_direction"):
        if not getattr(main, field) or not getattr(auxiliary, field):
            raise ValueError(f"main and auxiliary {field} must be recorded")
    for field in (
        "sensor",
        "track",
        "frame",
        "orbit_direction",
        "reference_date",
        "dates",
        "polarizations",
        "geometry_type",
    ):
        if getattr(main, field) != getattr(auxiliary, field):
            raise ValueError(f"main and auxiliary {field} must match")
    main_frequency = _frequency(main, "main")
    auxiliary_frequency = _frequency(auxiliary, "auxiliary")
    if math.isclose(main_frequency, auxiliary_frequency, rel_tol=1e-12):
        raise ValueError("main and auxiliary frequencies must be different")
    if len(aoi_digest) != 64 or any(value not in "0123456789abcdef" for value in aoi_digest):
        raise ValueError("aoi_digest must be lowercase SHA-256 hexadecimal")
    distance = float(buffer_km)
    if not math.isfinite(distance) or distance < 0.0:
        raise ValueError("buffer_km must be finite and non-negative")
    main_aoi, main_buffer = _crop_identity(main, main_path, "main")
    auxiliary_aoi, auxiliary_buffer = _crop_identity(
        auxiliary, auxiliary_path, "auxiliary"
    )
    if main_aoi != aoi_digest or auxiliary_aoi != aoi_digest:
        raise ValueError("main and auxiliary AOI crop identity must match")
    if not math.isclose(main_buffer, distance) or not math.isclose(
        auxiliary_buffer, distance
    ):
        raise ValueError("main and auxiliary crop buffer must match")
    low, high = (
        ("main", "auxiliary")
        if main_frequency < auxiliary_frequency
        else ("auxiliary", "main")
    )
    return DualFrequencyBundle(
        schema_version=1,
        main_manifest=main_path,
        auxiliary_manifest=auxiliary_path,
        main_frequency_hz=main_frequency,
        auxiliary_frequency_hz=auxiliary_frequency,
        low_frequency_role=low,
        high_frequency_role=high,
        sensor=main.sensor,
        track=main.track,
        frame=main.frame,
        orbit_direction=main.orbit_direction,
        reference_date=main.reference_date,
        dates=main.dates,
        polarizations=main.polarizations,
        aoi_digest=aoi_digest,
        buffer_km=distance,
    )


def _payload(bundle: DualFrequencyBundle, root: Path) -> dict[str, object]:
    return {
        "schema_version": bundle.schema_version,
        "main_manifest": os.path.relpath(bundle.main_manifest, root),
        "auxiliary_manifest": os.path.relpath(bundle.auxiliary_manifest, root),
        "main_frequency_hz": bundle.main_frequency_hz,
        "auxiliary_frequency_hz": bundle.auxiliary_frequency_hz,
        "low_frequency_role": bundle.low_frequency_role,
        "high_frequency_role": bundle.high_frequency_role,
        "sensor": bundle.sensor,
        "track": bundle.track,
        "frame": bundle.frame,
        "orbit_direction": bundle.orbit_direction,
        "reference_date": bundle.reference_date,
        "dates": list(bundle.dates),
        "polarizations": list(bundle.polarizations),
        "aoi_digest": bundle.aoi_digest,
        "buffer_km": bundle.buffer_km,
    }


def write_dual_frequency_bundle(bundle: DualFrequencyBundle, path: str | Path) -> Path:
    """Revalidate references and atomically write deterministic relative-path JSON."""

    if not isinstance(bundle, DualFrequencyBundle):
        raise TypeError("bundle must be DualFrequencyBundle")
    rebuilt = build_dual_frequency_bundle(
        bundle.main_manifest, bundle.auxiliary_manifest, bundle.aoi_digest, bundle.buffer_km
    )
    if rebuilt != bundle:
        raise ValueError("dual-frequency bundle metadata no longer matches its stack manifests")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(_payload(bundle, destination.parent), indent=2, sort_keys=True) + "\n").encode()
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


def read_dual_frequency_bundle(path: str | Path) -> DualFrequencyBundle:
    """Read a relationship JSON, resolve its relative paths, and revalidate both stacks."""

    source = Path(path).expanduser().resolve(strict=True)
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported dual-frequency bundle schema")
    main = (source.parent / str(document["main_manifest"])).resolve()
    auxiliary = (source.parent / str(document["auxiliary_manifest"])).resolve()
    bundle = build_dual_frequency_bundle(
        main,
        auxiliary,
        str(document["aoi_digest"]),
        float(document["buffer_km"]),
    )
    if _payload(bundle, source.parent) != document:
        raise ValueError("dual-frequency bundle metadata does not match referenced stacks")
    return bundle


__all__ = [
    "DualFrequencyBundle",
    "build_dual_frequency_bundle",
    "read_dual_frequency_bundle",
    "write_dual_frequency_bundle",
]
