"""Sentinel-1 BURST 分组校验与 SAFE 原子发布。

功能概述:
    依据已审核查询和已验证来源清单分组 BURST；所有输入身份、摘要和文件通过后才
    调用 ``burst2safe``，并只把结构完整且输入签名一致的 SAFE 发布到逐轨目录。

函数说明:
    ``Sentinel1BurstGroup`` 保存一个合并组；``Sentinel1MergeResult`` 保存发布结果；
    ``validate_burst_group`` 执行调用前检查；``merge_sentinel1_bursts`` 原子生成 SAFE。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .download import validate_sentinel1_download
from .manifest import validate_query_for_download
from .models import Sentinel1AcquisitionCase, Sentinel1Product, Sentinel1ProductType
from .query import Sentinel1QueryResult


@dataclass(frozen=True)
class Sentinel1BurstGroup:
    """One date/track/merge-group inventory ready for burst2safe."""

    key: tuple[str, int, str]
    products: tuple[Sentinel1Product, ...]
    source_paths: tuple[Path, ...]
    input_signature: str


@dataclass(frozen=True)
class Sentinel1MergeResult:
    """SAFE directories and manifest published by one merge operation."""

    safe_directories: tuple[Path, ...]
    safe_manifest: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_source_manifest(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid Sentinel-1 source manifest: {path}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError(f"unsupported Sentinel-1 source manifest: {path}")
    if not isinstance(document.get("products"), list):
        raise ValueError(f"invalid Sentinel-1 source manifest products: {path}")
    return document


def validate_burst_group(
    products: Sequence[Sentinel1Product],
    download_dir: str | Path,
    source_manifest: str | Path,
) -> Sentinel1BurstGroup:
    """Validate one complete, unique BURST group and all owned source files."""

    ordered = tuple(sorted(products, key=lambda item: (item.swath or "", item.relative_burst_id or 0, item.product_id)))
    if not ordered:
        raise ValueError("BURST group is empty")
    first = ordered[0]
    if first.product_type is not Sentinel1ProductType.BURST:
        raise ValueError(f"Sentinel-1 product {first.product_id} is not BURST")
    for product in ordered:
        if (
            product.product_type is not Sentinel1ProductType.BURST
            or not product.swath
            or product.burst_index is None
            or product.relative_burst_id is None
            or not product.full_burst_id
            or not product.merge_group
        ):
            raise ValueError(f"Sentinel-1 product {product.product_id} has incomplete burst identity")
        if (
            product.acquisition_date != first.acquisition_date
            or product.relative_orbit != first.relative_orbit
            or product.absolute_orbit != first.absolute_orbit
            or product.merge_group != first.merge_group
        ):
            raise ValueError("BURST group mixes dates, tracks, absolute orbits, or merge identities")
    full_ids = tuple(item.full_burst_id for item in ordered)
    if len(set(full_ids)) != len(full_ids):
        raise ValueError("duplicate full burst identity in merge group")
    product_ids = tuple(item.product_id for item in ordered)
    if len(set(product_ids)) != len(product_ids):
        raise ValueError("duplicate Sentinel-1 burst product in merge group")
    if not any(item.roi_match for item in ordered):
        raise ValueError("BURST group contains no research-area match")

    root = Path(download_dir).resolve()
    document = _load_source_manifest(Path(source_manifest))
    records = document["products"]
    by_id = {str(record.get("product_id")): record for record in records if isinstance(record, dict)}
    paths: list[Path] = []
    signature_records: list[dict[str, object]] = []
    for product in ordered:
        record = by_id.get(product.product_id)
        if record is None:
            raise ValueError(f"Sentinel-1 product {product.product_id} is missing from source manifest")
        relative = Path(str(record.get("file", "")))
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"Sentinel-1 product {product.product_id} source file is missing")
        expected_size = record.get("size_bytes")
        expected_hash = str(record.get("sha256", ""))
        actual_hash = _sha256(path)
        if path.stat().st_size != expected_size or actual_hash != expected_hash:
            raise ValueError(f"Sentinel-1 product {product.product_id} source digest mismatch")
        validate_sentinel1_download(product, path)
        paths.append(path)
        signature_records.append({"product_id": product.product_id, "sha256": actual_hash})
    signature_payload = json.dumps(
        {
            "date": first.acquisition_date,
            "track": first.relative_orbit,
            "merge_group": first.merge_group,
            "products": signature_records,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return Sentinel1BurstGroup(
        key=(first.acquisition_date, first.relative_orbit, first.merge_group),
        products=ordered,
        source_paths=tuple(paths),
        input_signature=hashlib.sha256(signature_payload).hexdigest(),
    )


def _default_runner(granules: Sequence[str], work_dir: Path) -> Path:
    from burst2safe.burst2safe import burst2safe

    return Path(burst2safe(granules=granules, keep_files=True, work_dir=work_dir))


def _validate_safe(path: Path) -> None:
    if not path.is_dir() or path.suffix != ".SAFE":
        raise ValueError("burst2safe did not return one SAFE directory")
    required_file = path / "manifest.safe"
    annotations = tuple((path / "annotation").glob("*")) if (path / "annotation").is_dir() else ()
    measurements = tuple((path / "measurement").glob("*")) if (path / "measurement").is_dir() else ()
    if not required_file.is_file() or required_file.stat().st_size == 0:
        raise ValueError(f"incomplete SAFE manifest: {path.name}")
    if not any(item.is_file() and item.stat().st_size > 0 for item in annotations):
        raise ValueError(f"incomplete SAFE annotation: {path.name}")
    if not any(item.is_file() and item.stat().st_size > 0 for item in measurements):
        raise ValueError(f"incomplete SAFE measurement: {path.name}")


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(name).unlink(missing_ok=True)
        raise


def merge_sentinel1_bursts(
    case: Sentinel1AcquisitionCase,
    query: Sentinel1QueryResult,
    source_manifest: str | Path,
    *,
    runner: Callable[[Sequence[str], Path], Path] = _default_runner,
    progress_sink: Callable[[str], None] = print,
) -> Sentinel1MergeResult:
    """Validate, merge, and atomically publish every selected BURST group."""

    if case.product_type is not Sentinel1ProductType.BURST:
        raise ValueError("Sentinel-1 merge is available only for BURST products")
    validate_query_for_download(case, query.manifest)
    if tuple(item.product_id for item in query.selected_products) != query.manifest.selected_product_ids:
        raise ValueError("Sentinel-1 selected products do not match query manifest")
    source_path = Path(source_manifest)
    source_document = _load_source_manifest(source_path)
    if source_document.get("case_name") != case.name or source_document.get("query_fingerprint") != query.manifest.fingerprint:
        raise ValueError("stale Sentinel-1 source manifest; run download again")
    grouped: dict[tuple[str, int, str], list[Sentinel1Product]] = defaultdict(list)
    for product in query.selected_products:
        grouped[(product.acquisition_date, product.relative_orbit, product.merge_group or "")].append(product)
    groups = tuple(
        validate_burst_group(grouped[key], case.download_dir, source_path)
        for key in sorted(grouped)
    )
    if not groups:
        raise ValueError("no Sentinel-1 BURST groups selected")

    manifest_path = case.download_dir / "safe_manifest.json"
    existing_records: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing_records = {str(item["group_key"]): item for item in existing.get("groups", [])}
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"invalid Sentinel-1 SAFE manifest: {manifest_path}") from error

    published: list[Path] = []
    records: list[dict[str, object]] = []
    for index, group in enumerate(groups, start=1):
        date, track, merge_group = group.key
        label = f"{date} track {track} {merge_group}"
        key_text = f"{date}|{track}|{merge_group}"
        previous = existing_records.get(key_text)
        if previous is not None:
            safe = case.download_dir / str(previous.get("safe", ""))
            marker = safe / "GEODESY_SOURCE.json"
            try:
                marker_document = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(f"stale SAFE metadata for {label}") from error
            if marker_document.get("input_signature") != group.input_signature:
                raise ValueError(f"stale SAFE input signature for {label}; use a new destination")
            _validate_safe(safe)
            published.append(safe)
            records.append(previous)
            progress_sink(f"[{index}/{len(groups)}] {label}: complete")
            continue

        orbit_dir = case.download_dir / f"Orbit_{track:03d}"
        orbit_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".merge-", suffix=".stage", dir=orbit_dir))
        progress_sink(f"[{index}/{len(groups)}] {label}: merging")
        try:
            for product, source in zip(group.products, group.source_paths, strict=True):
                link = staging / f"{product.product_id}{Path(product.file_name).suffix}"
                link.symlink_to(source)
            safe_stage = Path(runner(tuple(item.product_id for item in group.products), staging)).resolve()
            if staging.resolve() not in safe_stage.parents:
                raise ValueError("burst2safe output is outside the merge staging directory")
            _validate_safe(safe_stage)
            marker_document = {
                "schema_version": 1,
                "input_signature": group.input_signature,
                "query_fingerprint": query.manifest.fingerprint,
                "product_ids": [item.product_id for item in group.products],
            }
            _atomic_json(safe_stage / "GEODESY_SOURCE.json", marker_document)
            destination = orbit_dir / safe_stage.name
            if destination.exists():
                raise ValueError(f"SAFE destination already exists: {destination}")
            os.replace(safe_stage, destination)
            published.append(destination)
            record = {
                "group_key": key_text,
                "date": date,
                "track": track,
                "merge_group": merge_group,
                "input_signature": group.input_signature,
                "safe": destination.relative_to(case.download_dir).as_posix(),
                "product_ids": [item.product_id for item in group.products],
                "status": "merged",
            }
            records.append(record)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        progress_sink(f"[{index}/{len(groups)}] {label}: complete")

    _atomic_json(manifest_path, {
        "schema_version": 1,
        "case_name": case.name,
        "query_fingerprint": query.manifest.fingerprint,
        "groups": records,
    })
    if not case.keep_burst_files:
        for group in groups:
            for path in group.source_paths:
                path.unlink()
    return Sentinel1MergeResult(tuple(published), manifest_path)


__all__ = [
    "Sentinel1BurstGroup",
    "Sentinel1MergeResult",
    "merge_sentinel1_bursts",
    "validate_burst_group",
]
