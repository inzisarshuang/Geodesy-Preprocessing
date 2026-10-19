"""Sentinel-1 安全续传、格式校验和来源清单发布。

功能概述:
    仅消费通过指纹校验的选择表，将文件下载到逐轨目录的 ``.part``，安全处理 Range
    续传，验证产品大小、身份和格式后原子改名，并发布不含 URL 或凭据的来源清单。

函数说明:
    ``Sentinel1DownloadResult`` 保存下载结果；``validate_sentinel1_download`` 检查
    BURST/SLC/GRD 文件；``download_sentinel1`` 并发下载并报告统一逐景进度。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .manifest import validate_query_for_download
from .layout import resolve_download_track_dir
from .models import Sentinel1AcquisitionCase, Sentinel1Product, Sentinel1ProductType
from .query import Sentinel1QueryResult


@dataclass(frozen=True)
class Sentinel1DownloadResult:
    """Files and source manifest published by one successful operation."""

    download_dir: Path
    files: tuple[Path, ...]
    source_manifest: Path


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_filename(product: Sentinel1Product) -> str:
    name = Path(product.file_name).name
    if not name or name != product.file_name or name in {".", ".."}:
        raise ValueError(f"Sentinel-1 product {product.product_id} has unsafe filename")
    return name


def _zip_inventory(product: Sentinel1Product, path: Path) -> dict[str, object]:
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise ValueError(f"archive member is corrupt: {bad}")
            names = tuple(name for name in archive.namelist() if not name.endswith("/"))
    except (OSError, zipfile.BadZipFile, ValueError) as error:
        raise ValueError(f"Sentinel-1 product {product.product_id} ZIP archive is invalid") from error
    if product.product_type is Sentinel1ProductType.BURST:
        rasters = tuple(name for name in names if name.lower().endswith((".tif", ".tiff")))
        if not rasters or not any(product.product_id in PurePosixPath(name).stem for name in rasters):
            raise ValueError(f"Sentinel-1 product {product.product_id} archive identity mismatch")
        return {"kind": "BURST_ZIP", "members": len(names)}
    safe_roots = {
        PurePosixPath(name).parts[0]
        for name in names
        if PurePosixPath(name).parts and PurePosixPath(name).parts[0].endswith(".SAFE")
    }
    if len(safe_roots) != 1:
        raise ValueError(f"Sentinel-1 product {product.product_id} archive must contain one SAFE")
    root = next(iter(safe_roots))
    if PurePosixPath(root).stem != product.product_id:
        raise ValueError(f"Sentinel-1 product {product.product_id} archive identity mismatch")
    if f"{root}/manifest.safe" not in names:
        raise ValueError(f"Sentinel-1 product {product.product_id} SAFE manifest is missing")
    measurement = tuple(name for name in names if name.startswith(f"{root}/measurement/") and not name.endswith("/"))
    if not measurement:
        raise ValueError(f"Sentinel-1 product {product.product_id} SAFE measurement is missing")
    if product.product_type is Sentinel1ProductType.SLC:
        annotation = tuple(name for name in names if name.startswith(f"{root}/annotation/") and name.lower().endswith(".xml"))
        if not annotation:
            raise ValueError(f"Sentinel-1 product {product.product_id} SLC annotation is missing")
        kind = "SLC_SAFE_ZIP"
    else:
        kind = "GRD_SAFE_ZIP"
    return {"kind": kind, "members": len(names), "safe_root": root}


def validate_sentinel1_download(
    product: Sentinel1Product, path: str | Path
) -> dict[str, object]:
    """Validate identity and structure; retain provider size as advisory metadata."""

    source = Path(path)
    if not source.is_file():
        raise ValueError(f"Sentinel-1 product {product.product_id} file is missing")
    size = source.stat().st_size
    suffix = Path(product.file_name).suffix.lower()
    if suffix == ".zip":
        inventory = _zip_inventory(product, source)
    else:
        if product.product_type is not Sentinel1ProductType.BURST or suffix not in {".tif", ".tiff"}:
            raise ValueError(f"Sentinel-1 product {product.product_id} has unexpected file format")
        if product.product_id not in Path(product.file_name).stem:
            raise ValueError(f"Sentinel-1 product {product.product_id} raster identity mismatch")
        try:
            import rasterio

            with rasterio.open(source) as dataset:
                if dataset.width < 1 or dataset.height < 1 or dataset.count < 1:
                    raise ValueError("empty raster")
                width, height, bands = dataset.width, dataset.height, dataset.count
        except Exception as error:
            raise ValueError(f"Sentinel-1 product {product.product_id} BURST raster is invalid") from error
        inventory = {"kind": "BURST_RASTER", "width": width, "height": height, "bands": bands}
    return {
        **inventory,
        "provider_size_bytes": product.size_bytes,
        "actual_size_bytes": size,
        "size_matches_provider": size == product.size_bytes,
    }


def _stream_once(session, product: Sentinel1Product, part: Path) -> None:
    resume = part.stat().st_size if part.exists() else 0
    if resume >= product.size_bytes:
        part.unlink()
        resume = 0
    headers = {"Range": f"bytes={resume}-"} if resume else {}
    try:
        response = session.get(product.url, headers=headers, stream=True)
    except KeyboardInterrupt:
        raise
    except Exception:
        raise RuntimeError(f"download failed for {product.product_id}: network request failed") from None
    try:
        status = int(getattr(response, "status_code", 0))
        if status < 200 or status >= 400:
            raise RuntimeError(f"download failed for {product.product_id}: HTTP {status}")
        append = resume > 0 and status == 206
        if append:
            content_range = str(getattr(response, "headers", {}).get("Content-Range", ""))
            if not content_range.startswith(f"bytes {resume}-"):
                raise RuntimeError(f"download failed for {product.product_id}: invalid range response")
        elif status != 200:
            raise RuntimeError(f"download failed for {product.product_id}: unexpected HTTP {status}")
        with part.open("ab" if append else "wb") as stream:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if block:
                    stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
    except (KeyboardInterrupt, RuntimeError):
        raise
    except Exception:
        raise RuntimeError(f"download failed for {product.product_id}: streaming error") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _download_one(case: Sentinel1AcquisitionCase, session, product: Sentinel1Product) -> tuple[Path, str, dict[str, object]]:
    directory = resolve_download_track_dir(case, product.relative_orbit)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / _safe_filename(product)
    part = directory / f"{target.name}.part"
    if target.exists():
        return target, "existing", validate_sentinel1_download(product, target)
    attempts = case.max_retries + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            _stream_once(session, product, part)
            inventory = validate_sentinel1_download(product, part)
            os.replace(part, target)
            return target, "downloaded", inventory
        except KeyboardInterrupt:
            raise
        except (RuntimeError, ValueError) as error:
            last_error = error
            if isinstance(error, ValueError):
                part.unlink(missing_ok=True)
            if attempt + 1 < attempts and case.retry_delay_seconds:
                time.sleep(case.retry_delay_seconds)
    raise last_error if last_error is not None else RuntimeError(
        f"download failed for {product.product_id}"
    )


def _write_manifest(path: Path, case: Sentinel1AcquisitionCase, query: Sentinel1QueryResult, records: list[dict[str, object]]) -> None:
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


def download_sentinel1(
    case: Sentinel1AcquisitionCase,
    query: Sentinel1QueryResult,
    session,
    progress_sink: Callable[[str], None] = print,
) -> Sentinel1DownloadResult:
    """Download every reviewed product and atomically publish a source manifest."""

    validate_query_for_download(case, query.manifest)
    products = query.selected_products
    if not products:
        raise ValueError("no Sentinel-1 products selected; review the query before download")
    if tuple(item.product_id for item in products) != query.manifest.selected_product_ids:
        raise ValueError("Sentinel-1 selected products do not match query manifest")
    targets = tuple((item.relative_orbit, _safe_filename(item)) for item in products)
    if len(set(targets)) != len(targets):
        raise ValueError("duplicate Sentinel-1 download target filename")
    case.download_dir.mkdir(parents=True, exist_ok=True)
    for index, product in enumerate(products, start=1):
        progress_sink(f"[{index}/{len(products)}] {product.product_id}: downloading")
    with ThreadPoolExecutor(max_workers=case.download_threads) as executor:
        futures = [executor.submit(_download_one, case, session, product) for product in products]
        completed = [future.result() for future in futures]
    files: list[Path] = []
    records: list[dict[str, object]] = []
    for index, (product, (path, status, inventory)) in enumerate(zip(products, completed, strict=True), start=1):
        files.append(path)
        records.append({
            "product_id": product.product_id,
            "product_type": product.product_type.value,
            "acquisition_date": product.acquisition_date,
            "relative_orbit": product.relative_orbit,
            "orbit_direction": product.orbit_direction,
            "polarizations": list(product.polarizations),
            "file": path.relative_to(case.download_dir).as_posix(),
            "provider_size_bytes": product.size_bytes,
            "size_bytes": path.stat().st_size,
            "sha256": _digest(path),
            "status": status,
            "validation": inventory,
        })
        progress_sink(f"[{index}/{len(products)}] {product.product_id}: complete")
    source_manifest = case.download_dir / "source_manifest.json"
    _write_manifest(source_manifest, case, query, records)
    return Sentinel1DownloadResult(case.download_dir, tuple(files), source_manifest)


__all__ = ["Sentinel1DownloadResult", "download_sentinel1", "validate_sentinel1_download"]
