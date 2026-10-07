"""NISAR Earthdata 流式下载、续传、校验和原子发布。

功能概述:
    仅消费通过指纹校验的筛选结果，以同目录 ``.part`` 文件安全续传；验证长度、
    可用校验和及产品对应的 HDF5 RSLC/GSLC 结构后原子改名，并发布不含凭据的来源清单。

函数说明:
    ``NisarDownloadResult`` 保存正式文件与来源清单路径；``download_nisar`` 使用注入的
    已认证会话下载全部已选产品并报告逐景进度。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from .hdf5 import NisarRslcInventory, inspect_nisar_rslc
from .gslc_hdf5 import NisarGslcInventory, inspect_nisar_gslc
from .manifest import validate_query_for_download
from .models import NisarGranule, NisarProductType, NisarSearchCase
from .query import NisarQueryResult


@dataclass(frozen=True)
class NisarDownloadResult:
    """Paths published by one successful NISAR download operation."""

    download_dir: Path
    files: tuple[Path, ...]
    source_manifest: Path


def _digest(path: Path, algorithm: str) -> str:
    try:
        digest = hashlib.new(algorithm)
    except ValueError as error:
        raise ValueError(f"unsupported NISAR checksum algorithm {algorithm}") from error
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit(SplitResult(parsed.scheme, hostname, parsed.path, "", ""))


def _target_name(granule: NisarGranule) -> str:
    name = Path(unquote(urlsplit(granule.url).path)).name
    if not name or name in {".", ".."} or not name.lower().endswith((".h5", ".hdf5")):
        raise ValueError(f"NISAR granule {granule.granule_id} URL has no HDF5 filename")
    return name


def _check_checksum(path: Path, granule: NisarGranule) -> None:
    if granule.checksum is None:
        return
    if ":" not in granule.checksum:
        raise ValueError(f"NISAR granule {granule.granule_id} checksum lacks an algorithm")
    algorithm, expected = granule.checksum.split(":", 1)
    actual = _digest(path, algorithm.lower())
    if actual.lower() != expected.strip().lower():
        raise ValueError(f"NISAR granule {granule.granule_id} checksum mismatch")


def _validate_file(
    path: Path, granule: NisarGranule
) -> NisarRslcInventory | NisarGslcInventory:
    size = path.stat().st_size
    if size != granule.size_bytes:
        raise ValueError(
            f"NISAR granule {granule.granule_id} size mismatch: "
            f"expected {granule.size_bytes}, got {size}"
        )
    _check_checksum(path, granule)
    if granule.product_type is NisarProductType.RSLC:
        inventory = inspect_nisar_rslc(path)
    elif granule.product_type is NisarProductType.GSLC:
        inventory = inspect_nisar_gslc(path)
    else:  # pragma: no cover - enum construction prevents this in normal use
        raise ValueError(f"unsupported NISAR product type {granule.product_type}")
    if (
        inventory.granule_id != granule.granule_id
        or inventory.track != granule.track
        or inventory.frame != granule.frame
        or inventory.orbit_direction != granule.orbit_direction
    ):
        label = (
            "granule identity"
            if inventory.granule_id != granule.granule_id
            else "metadata"
        )
        raise ValueError(
            f"NISAR granule {granule.granule_id} HDF5 {label} disagrees with query"
        )
    return inventory


def _download_one(session, granule: NisarGranule, part: Path) -> None:
    resume = part.stat().st_size if part.exists() else 0
    if resume >= granule.size_bytes:
        part.unlink()
        resume = 0
    headers = {"Range": f"bytes={resume}-"} if resume else {}
    try:
        response = session.get(granule.url, headers=headers, stream=True)
    except Exception:
        raise RuntimeError(
            f"download failed for {granule.granule_id}: network request failed"
        ) from None
    try:
        status = int(getattr(response, "status_code", 0))
        if status >= 400 or status < 200:
            raise RuntimeError(f"download failed for {granule.granule_id}: HTTP {status}")
        append = resume > 0 and status == 206
        if append:
            content_range = str(getattr(response, "headers", {}).get("Content-Range", ""))
            if not content_range.startswith(f"bytes {resume}-"):
                raise RuntimeError(
                    f"download failed for {granule.granule_id}: invalid range response"
                )
        elif status != 200:
            raise RuntimeError(
                f"download failed for {granule.granule_id}: unexpected HTTP {status}"
            )
        mode = "ab" if append else "wb"
        with part.open(mode) as stream:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if block:
                    stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(
            f"download failed for {granule.granule_id}: streaming error"
        ) from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _write_source_manifest(
    path: Path,
    case: NisarSearchCase,
    query: NisarQueryResult,
    records: list[dict[str, object]],
) -> None:
    document = {
        "schema_version": 1,
        "case_name": case.name,
        "query_fingerprint": query.manifest.fingerprint,
        "products": records,
    }
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


def download_nisar(
    case: NisarSearchCase,
    query: NisarQueryResult,
    session,
    progress_sink: Callable[[str], None] = print,
) -> NisarDownloadResult:
    """Download every selected RSLC or GSLC after validating the query identity."""

    validate_query_for_download(case, query.manifest)
    if not query.selected_granules:
        raise ValueError("no NISAR granules selected; review the query before download")
    selected_ids = tuple(item.granule_id for item in query.selected_granules)
    if selected_ids != query.manifest.selected_granule_ids:
        raise ValueError("NISAR selected granules do not match query manifest")
    if any(item.product_type is not case.product_type for item in query.selected_granules):
        raise ValueError(
            f"NISAR selected granules do not match requested {case.product_type.value} product type"
        )
    targets = tuple(_target_name(item) for item in query.selected_granules)
    if len(set(targets)) != len(targets):
        duplicate = next(name for name in targets if targets.count(name) > 1)
        raise ValueError(f"duplicate NISAR download target filename: {duplicate}")
    case.download_dir.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    records: list[dict[str, object]] = []
    total = len(query.selected_granules)
    for index, (granule, filename) in enumerate(
        zip(query.selected_granules, targets, strict=True), start=1
    ):
        target = case.download_dir / filename
        part = case.download_dir / f"{filename}.part"
        status = "existing"
        progress_sink(f"[{index}/{total}] {granule.granule_id}: downloading")
        if target.exists():
            inventory = _validate_file(target, granule)
        else:
            _download_one(session, granule, part)
            inventory = _validate_file(part, granule)
            os.replace(part, target)
            status = "downloaded"
        published.append(target)
        records.append(
            {
                "granule_id": granule.granule_id,
                "acquisition_date": granule.acquisition_date,
                "track": granule.track,
                "frame": granule.frame,
                "orbit_direction": granule.orbit_direction,
                "product_type": inventory.product_type,
                "product_specification_version": getattr(
                    inventory, "product_specification_version", None
                ),
                "bands": list(inventory.bands),
                "frequencies": sorted({item.frequency for item in inventory.channels}),
                "polarizations": sorted({item.polarization for item in inventory.channels}),
                "url": _safe_url(granule.url),
                "size_bytes": target.stat().st_size,
                "source_checksum": granule.checksum,
                "sha256": _digest(target, "sha256"),
                "status": status,
                "file": target.name,
            }
        )
        progress_sink(f"[{index}/{total}] {granule.granule_id}: complete")
    source_manifest = case.download_dir / "source_manifest.json"
    _write_source_manifest(source_manifest, case, query, records)
    return NisarDownloadResult(
        download_dir=case.download_dir,
        files=tuple(published),
        source_manifest=source_manifest,
    )


__all__ = ["NisarDownloadResult", "download_nisar"]
