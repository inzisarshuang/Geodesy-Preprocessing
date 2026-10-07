"""NISAR 查询指纹与下载前一致性校验。

功能概述:
    对影响搜索结果的配置和 KML 内容生成稳定 SHA-256 指纹，并记录筛选结果文件摘要，
    防止配置、研究区或 CSV 改动后继续下载旧结果。

函数说明:
    ``NisarQueryManifest`` 保存查询身份；``query_fingerprint`` 计算配置指纹；
    ``write_query_manifest`` 和 ``read_query_manifest`` 写读确定性 JSON；
    ``validate_query_for_download`` 在联网前验证查询仍然有效。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .models import NisarSearchCase


_SCHEMA_VERSION = 1


def _filtered_csv(case: NisarSearchCase) -> str:
    return f"NISAR_{case.product_type.value}_filtered.csv"


@dataclass(frozen=True)
class NisarQueryManifest:
    """Immutable identity and artifact digests for one published query."""

    schema_version: int
    case_name: str
    fingerprint: str
    kml_sha256: str
    selected_granule_ids: tuple[str, ...]
    artifact_digests: tuple[tuple[str, str], ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint_payload(case: NisarSearchCase, kml_digest: str) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "kml_sha256": kml_digest,
        "start_date": case.start_date,
        "end_date": case.end_date,
        "product_type": case.product_type.value,
        "band": case.band,
        "polarizations": list(case.polarizations),
        "provider": case.provider,
        "frequency_mode": case.frequency_mode.value,
        "tracks": None if case.tracks is None else list(case.tracks),
        "frames": None if case.frames is None else list(case.frames),
        "orbit_direction": case.orbit_direction,
    }


def query_fingerprint(case: NisarSearchCase, kml_digest: str) -> str:
    """Return a stable SHA-256 over every search-affecting option and KML digest."""

    if len(kml_digest) != 64 or any(character not in "0123456789abcdef" for character in kml_digest):
        raise ValueError("KML digest must be a lowercase SHA-256 hexadecimal string")
    payload = json.dumps(
        _fingerprint_payload(case, kml_digest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def write_query_manifest(manifest: NisarQueryManifest, path: str | Path) -> Path:
    """Write deterministic query-manifest JSON into an existing staging directory."""

    destination = Path(path)
    document = {
        "schema_version": manifest.schema_version,
        "case_name": manifest.case_name,
        "fingerprint": manifest.fingerprint,
        "kml_sha256": manifest.kml_sha256,
        "selected_granule_ids": list(manifest.selected_granule_ids),
        "artifact_digests": dict(manifest.artifact_digests),
    }
    destination.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def read_query_manifest(path: str | Path) -> NisarQueryManifest:
    """Read one schema-v1 query manifest and reject malformed identity fields."""

    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid NISAR query manifest: {source}") from error
    if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError(f"unsupported NISAR query manifest schema: {source}")
    artifacts = document.get("artifact_digests")
    granules = document.get("selected_granule_ids")
    if not isinstance(artifacts, dict) or not isinstance(granules, list):
        raise ValueError(f"invalid NISAR query manifest content: {source}")
    try:
        return NisarQueryManifest(
            schema_version=_SCHEMA_VERSION,
            case_name=str(document["case_name"]),
            fingerprint=str(document["fingerprint"]),
            kml_sha256=str(document["kml_sha256"]),
            selected_granule_ids=tuple(str(item) for item in granules),
            artifact_digests=tuple(sorted((str(key), str(value)) for key, value in artifacts.items())),
        )
    except KeyError as error:
        raise ValueError(f"invalid NISAR query manifest content: {source}") from error


def validate_query_for_download(
    case: NisarSearchCase, manifest: NisarQueryManifest
) -> None:
    """Reject stale KML/options or a modified filtered CSV before network access."""

    kml_digest = _sha256_file(case.kml_file)
    expected = query_fingerprint(case, kml_digest)
    if (
        manifest.schema_version != _SCHEMA_VERSION
        or manifest.case_name != case.name
        or manifest.kml_sha256 != kml_digest
        or manifest.fingerprint != expected
    ):
        raise ValueError("stale NISAR query fingerprint or KML; run query again")
    artifacts = dict(manifest.artifact_digests)
    filtered_name = _filtered_csv(case)
    expected_csv = artifacts.get(filtered_name)
    filtered = case.query_dir / filtered_name
    if expected_csv is None or not filtered.is_file() or _sha256_file(filtered) != expected_csv:
        raise ValueError("NISAR filtered CSV changed after query; run query again")


__all__ = [
    "NisarQueryManifest",
    "query_fingerprint",
    "read_query_manifest",
    "validate_query_for_download",
    "write_query_manifest",
]
