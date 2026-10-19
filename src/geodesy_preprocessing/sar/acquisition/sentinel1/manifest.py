"""Sentinel-1 查询指纹与下载前一致性校验。

功能概述:
    对所有查询参数、研究区内容和发布文件生成稳定摘要，阻止修改 CFG、KML 或 CSV
    后继续使用旧查询结果。

函数说明:
    ``Sentinel1QueryManifest`` 保存查询身份；``query_fingerprint`` 计算指纹；
    ``sha256_file`` 计算文件摘要；``aoi_sha256`` 计算研究区摘要；
    ``write_query_manifest`` 与 ``read_query_manifest`` 写读清单；
    ``validate_query_for_download`` 验证下载输入未发生变化。
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from .models import Sentinel1AcquisitionCase


_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Sentinel1QueryManifest:
    """Immutable identity and artifact digests for one published query."""

    schema_version: int
    case_name: str
    fingerprint: str
    aoi_sha256: str
    selected_product_ids: tuple[str, ...]
    artifact_digests: tuple[tuple[str, str], ...]


def sha256_file(path: str | Path) -> str:
    """Return a lowercase SHA-256 digest without loading the whole file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def aoi_sha256(case: Sentinel1AcquisitionCase) -> str:
    """Digest the KML bytes or canonical bbox that defines one case AOI."""

    if case.kml_file is not None:
        return sha256_file(case.kml_file)
    payload = json.dumps(list(case.bbox), separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def query_fingerprint(case: Sentinel1AcquisitionCase, aoi_digest: str) -> str:
    """Return a stable digest over every search-affecting option and AOI."""

    if len(aoi_digest) != 64 or any(character not in "0123456789abcdef" for character in aoi_digest):
        raise ValueError("AOI digest must be a lowercase SHA-256 hexadecimal string")
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "aoi_sha256": aoi_digest,
        "start_date": case.start_date,
        "end_date": case.end_date,
        "product_type": case.product_type.value,
        "beam_mode": case.beam_mode,
        "polarizations": None if case.polarizations is None else list(case.polarizations),
        "provider": case.provider,
        "tracks": None if case.tracks is None else list(case.tracks),
        "orbit_direction": case.orbit_direction,
        "coverage_threshold": case.coverage_threshold,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def write_query_manifest(manifest: Sentinel1QueryManifest, path: str | Path) -> Path:
    """Write deterministic manifest JSON inside an existing staging directory."""

    destination = Path(path)
    document = {
        "schema_version": manifest.schema_version,
        "case_name": manifest.case_name,
        "fingerprint": manifest.fingerprint,
        "aoi_sha256": manifest.aoi_sha256,
        "selected_product_ids": list(manifest.selected_product_ids),
        "artifact_digests": dict(manifest.artifact_digests),
    }
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def read_query_manifest(path: str | Path) -> Sentinel1QueryManifest:
    """Read one schema-v1 query manifest and reject malformed fields."""

    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid Sentinel-1 query manifest: {source}") from error
    if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError(f"unsupported Sentinel-1 query manifest schema: {source}")
    artifacts = document.get("artifact_digests")
    products = document.get("selected_product_ids")
    if not isinstance(artifacts, dict) or not isinstance(products, list):
        raise ValueError(f"invalid Sentinel-1 query manifest content: {source}")
    try:
        return Sentinel1QueryManifest(
            schema_version=_SCHEMA_VERSION,
            case_name=str(document["case_name"]),
            fingerprint=str(document["fingerprint"]),
            aoi_sha256=str(document["aoi_sha256"]),
            selected_product_ids=tuple(str(item) for item in products),
            artifact_digests=tuple(sorted((str(key), str(value)) for key, value in artifacts.items())),
        )
    except KeyError as error:
        raise ValueError(f"invalid Sentinel-1 query manifest content: {source}") from error


def validate_query_for_download(
    case: Sentinel1AcquisitionCase, manifest: Sentinel1QueryManifest
) -> None:
    """Reject stale options/AOI or a modified selected CSV before download."""

    digest = aoi_sha256(case)
    if manifest.case_name != case.name or manifest.aoi_sha256 != digest:
        raise ValueError("stale Sentinel-1 query fingerprint or KML; run query again")
    narrowed_track: int | None = None
    if manifest.fingerprint != query_fingerprint(case, digest):
        if (
            case.tracks is None
            or len(case.tracks) != 1
            or manifest.fingerprint
            != query_fingerprint(replace(case, tracks=None), digest)
        ):
            raise ValueError("stale Sentinel-1 query fingerprint or KML; run query again")
        narrowed_track = case.tracks[0]
    selected_name = f"S1_{case.product_type.value}_selected.csv"
    expected = dict(manifest.artifact_digests).get(selected_name)
    selected = case.query_dir / selected_name
    if expected is None or not selected.is_file() or sha256_file(selected) != expected:
        raise ValueError("Sentinel-1 selected CSV changed after query; run query again")
    if narrowed_track is not None:
        with selected.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        try:
            selected_tracks = {int(row["relative_orbit"]) for row in rows}
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "Sentinel-1 selected CSV has invalid relative_orbit values; run query again"
            ) from error
        if selected_tracks != {narrowed_track}:
            raise ValueError(
                "Sentinel-1 selected CSV does not match the configured track; run query again"
            )


__all__ = [
    "Sentinel1QueryManifest",
    "aoi_sha256",
    "query_fingerprint",
    "read_query_manifest",
    "sha256_file",
    "validate_query_for_download",
    "write_query_manifest",
]
