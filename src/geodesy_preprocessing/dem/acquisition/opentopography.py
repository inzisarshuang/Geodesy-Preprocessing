"""Secure OpenTopography Global DEM downloads.

功能概述:
    校验数据集和 API 地址，安全读取密钥，流式下载并检查 GeoTIFF，写出无密钥元数据。

函数说明:
    ``normalize_dem_type`` 规范数据集；``resolve_api_key`` 读取密钥；
    ``validate_api_url`` 限制可信地址；``build_request_params`` 构建请求；
    ``create_http_session`` 配置重试；``is_tiff_signature`` 检查文件头；
    ``validate_geotiff`` 检查栅格；``sha256_file`` 计算摘要；
    ``download_dem`` 下载并安全发布；``write_metadata`` 写运行信息。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import rasterio
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import cfg_str
from ..extent import BoundingBox


DEFAULT_API_URL = "https://portal.opentopography.org/API/globaldem"
DEFAULT_API_KEY_ENV = "OPENTOPO_API_KEY"

DEM_ALIASES = {
    "COP30": "COP30",
    "COP90": "COP90",
    "NASADEM": "NASADEM",
    "SRTM_GL1": "SRTMGL1",
    "SRTMGL1": "SRTMGL1",
    "SRTM_GL1_ELLIPSOIDAL": "SRTMGL1_E",
    "SRTM_GL1_ELLIP": "SRTMGL1_E",
    "SRTM_GL1_E": "SRTMGL1_E",
    "SRTMGL1_E": "SRTMGL1_E",
    "SRTM_GL3": "SRTMGL3",
    "SRTMGL3": "SRTMGL3",
    "AW3D30": "AW3D30",
    "AW3D30_E": "AW3D30_E",
    "EU_DTM": "EU_DTM",
    "GEDTM30": "GEDTM30",
    "GEDI_L3": "GEDI_L3",
    "ANADEM": "ANADEM",
    "CA_MRDEM_DSM": "CA_MRDEM_DSM",
    "CA_MRDEM_DTM": "CA_MRDEM_DTM",
    "SRTM15PLUS": "SRTM15Plus",
    "GEBCOICETOPO": "GEBCOIceTopo",
    "GEBCOSUBICETOPO": "GEBCOSubIceTopo",
}
OFFICIAL_DEM_TYPES = frozenset(DEM_ALIASES.values())
OFFICIAL_OUTPUT_FORMATS = frozenset({"GTiff", "AAIGrid", "HFA"})
KNOWN_30M_DATASETS = frozenset(
    {
        "COP30",
        "NASADEM",
        "SRTMGL1",
        "SRTMGL1_E",
        "AW3D30",
        "AW3D30_E",
        "EU_DTM",
        "GEDTM30",
        "ANADEM",
        "CA_MRDEM_DSM",
        "CA_MRDEM_DTM",
    }
)
MAX_AREA_30M_KM2 = 450_000.0


def _safe_error_text(value: object, api_key: str) -> str:
    message = str(value)
    if api_key:
        message = message.replace(api_key, "<redacted>")
    return re.sub(
        r"(?i)(API_Key=)[^&\s]+", r"\1<redacted>", message
    )


def normalize_dem_type(value: str) -> str:
    """Map accepted friendly dataset names to official API identifiers."""

    raw = value.strip()
    if not raw:
        raise ValueError("dem_type cannot be empty")
    key = raw.upper().replace("-", "_").replace(" ", "_")
    normalized = DEM_ALIASES.get(key, raw)
    if normalized not in OFFICIAL_DEM_TYPES:
        raise ValueError(
            f"unsupported OpenTopography dem_type {value!r}; expected one of "
            f"{sorted(OFFICIAL_DEM_TYPES)}"
        )
    return normalized


def resolve_api_key(cfg: Mapping[str, str]) -> tuple[str, str]:
    """Return the credential and a safe description of its source."""

    direct = cfg_str(cfg, "api_key")
    if direct:
        return direct, "cfg:api_key"
    environment_name = cfg_str(cfg, "api_key_env", DEFAULT_API_KEY_ENV)
    if not environment_name:
        raise ValueError("api_key_env cannot be empty")
    value = os.getenv(environment_name, "").strip()
    if value:
        return value, f"environment:{environment_name}"
    raise RuntimeError(
        "OpenTopography API key not found.\n"
        f"Set environment variable {environment_name}, for example:\n"
        f'    export {environment_name}="YOUR_API_KEY"'
    )


def validate_api_url(api_url: str) -> str:
    """Allow credentials only on the official HTTPS host."""

    parsed = urlparse(api_url)
    if parsed.scheme != "https":
        raise ValueError("api_url must use HTTPS")
    if parsed.hostname != "portal.opentopography.org":
        raise ValueError("api_url host must be portal.opentopography.org")
    return api_url


def build_request_params(
    dem_type: str,
    bbox: BoundingBox,
    api_key: str,
    output_format: str = "GTiff",
) -> dict[str, object]:
    """Build one Global DEM API query without logging the result."""

    return {
        "demtype": normalize_dem_type(dem_type),
        "south": bbox.south,
        "north": bbox.north,
        "west": bbox.west,
        "east": bbox.east,
        "outputFormat": output_format,
        "API_Key": api_key,
    }


def create_http_session(retries: int, backoff_factor: float) -> requests.Session:
    """Create a requests session with bounded GET retries."""

    if retries < 0:
        raise ValueError("retries must be >= 0")
    if backoff_factor < 0:
        raise ValueError("backoff_factor must be >= 0")
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def is_tiff_signature(signature: bytes) -> bool:
    """Recognize classic TIFF and BigTIFF byte signatures."""

    return any(
        signature.startswith(marker)
        for marker in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
    )


def validate_geotiff(path: Path, requested_bbox: BoundingBox) -> dict[str, object]:
    """Validate one non-empty EPSG:4326 elevation raster covering the request."""

    with rasterio.open(path) as dataset:
        if dataset.count != 1 or dataset.width <= 0 or dataset.height <= 0:
            raise RuntimeError("downloaded GeoTIFF must contain one non-empty band")
        if dataset.crs is None or dataset.crs.to_epsg() != 4326:
            raise RuntimeError(
                f"downloaded GeoTIFF CRS must be EPSG:4326, got {dataset.crs}"
            )
        x_resolution = abs(float(dataset.transform.a))
        y_resolution = abs(float(dataset.transform.e))
        if x_resolution <= 0 or y_resolution <= 0:
            raise RuntimeError("downloaded GeoTIFF has an invalid pixel transform")
        bounds = dataset.bounds
        if (
            bounds.left > requested_bbox.west + 2 * x_resolution
            or bounds.right < requested_bbox.east - 2 * x_resolution
            or bounds.bottom > requested_bbox.south + 2 * y_resolution
            or bounds.top < requested_bbox.north - 2 * y_resolution
        ):
            raise RuntimeError(
                "downloaded GeoTIFF does not cover the requested bounding box: "
                f"raster={tuple(bounds)}, request={requested_bbox.as_dict()}"
            )
        if not any(
            dataset.read(1, window=window, masked=True).count() > 0
            for _, window in dataset.block_windows(1)
        ):
            raise RuntimeError("downloaded GeoTIFF contains no valid elevation cells")
        return {
            "crs": dataset.crs.to_string(),
            "width": dataset.width,
            "height": dataset.height,
            "dtype": dataset.dtypes[0],
            "nodata": dataset.nodata,
            "bounds": list(bounds),
            "resolution": [x_resolution, y_resolution],
        }


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_dem(
    *,
    api_url: str,
    dem_type: str,
    bbox: BoundingBox,
    output_path: Path,
    api_key: str,
    output_format: str = "GTiff",
    timeout_connect_sec: float = 30.0,
    timeout_read_sec: float = 600.0,
    retries: int = 4,
    backoff_factor: float = 2.0,
    chunk_mb: int = 4,
    overwrite: bool = False,
    verify_tiff: bool = True,
    session: requests.Session | None = None,
) -> Path:
    """Download, validate, and atomically publish one DEM raster."""

    validate_api_url(api_url)
    normalized_type = normalize_dem_type(dem_type)
    if timeout_connect_sec <= 0 or timeout_read_sec <= 0:
        raise ValueError("HTTP timeout values must be > 0")
    if chunk_mb <= 0:
        raise ValueError("chunk_mb must be > 0")
    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"output already exists: {output_path}; set overwrite=true or use --force"
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".part", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    owned_session = session is None
    active_session = session or create_http_session(retries, backoff_factor)
    response = None
    try:
        response = active_session.get(
            api_url,
            params=build_request_params(
                normalized_type, bbox, api_key, output_format=output_format
            ),
            stream=True,
            timeout=(timeout_connect_sec, timeout_read_sec),
        )
        if not response.ok:
            raise RuntimeError(
                "OpenTopography request failed: "
                f"HTTP {response.status_code}: "
                + _safe_error_text(response.text[:2000], api_key)
            )
        content_type = response.headers.get("Content-Type", "").lower()
        if "json" in content_type or content_type.startswith("text/"):
            raise RuntimeError(
                "OpenTopography returned text/JSON instead of a raster: "
                + _safe_error_text(response.text[:2000], api_key)
            )
        downloaded = 0
        with temporary_path.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=chunk_mb * 1024 * 1024):
                if chunk:
                    stream.write(chunk)
                    downloaded += len(chunk)
        print(f"[opentopo] downloaded={downloaded / 1024**2:.1f} MB complete")
        if downloaded == 0:
            raise RuntimeError("downloaded file is empty")
        if verify_tiff and output_format.lower() == "gtiff":
            if not is_tiff_signature(temporary_path.read_bytes()[:4]):
                raise RuntimeError("downloaded response is not a TIFF/GeoTIFF")
            validate_geotiff(temporary_path, bbox)
        temporary_path.replace(output_path)
    except requests.RequestException as error:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(
            "OpenTopography network request failed: "
            + _safe_error_text(error, api_key)
        ) from None
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        if response is not None:
            response.close()
        if owned_session:
            active_session.close()
    return output_path


def write_metadata(
    metadata_path: Path,
    *,
    config_path: Path,
    input_mode: str,
    input_kml: Path | None,
    kml_placemark: str | None,
    source_bbox: BoundingBox,
    request_bbox: BoundingBox,
    buffer_km: float,
    estimated_area_km2: float,
    api_url: str,
    dem_type: str,
    output_format: str,
    output_path: Path,
    api_key_source: str,
    source_vertical_datum: str = "unspecified",
    gamma_dem: Mapping[str, object] | None = None,
) -> None:
    """Atomically write reproducibility metadata without the credential value."""

    payload: dict[str, object] = {
        "tool": "geodesy-pre-dem",
        "config": str(config_path),
        "input_mode": input_mode,
        "input_kml": str(input_kml) if input_kml else None,
        "kml_placemark": kml_placemark,
        "source_bbox_wgs84": source_bbox.as_dict(),
        "request_bbox_wgs84": request_bbox.as_dict(),
        "buffer_km": buffer_km,
        "estimated_request_area_km2": estimated_area_km2,
        "api_url": api_url,
        "dem_type": dem_type,
        "source_vertical_datum": source_vertical_datum,
        "output_format": output_format,
        "output_file": str(output_path),
        "output_bytes": output_path.stat().st_size,
        "output_sha256": sha256_file(output_path),
        "api_key_source": api_key_source,
        "gamma_dem": dict(gamma_dem) if gamma_dem is not None else None,
    }
    if output_format == "GTiff":
        payload["raster"] = validate_geotiff(output_path, request_bbox)
    metadata_path = Path(metadata_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{metadata_path.name}.", suffix=".tmp", dir=metadata_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        Path(temporary_name).replace(metadata_path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


__all__ = [
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_API_URL",
    "DEM_ALIASES",
    "KNOWN_30M_DATASETS",
    "MAX_AREA_30M_KM2",
    "OFFICIAL_DEM_TYPES",
    "OFFICIAL_OUTPUT_FORMATS",
    "build_request_params",
    "create_http_session",
    "download_dem",
    "is_tiff_signature",
    "normalize_dem_type",
    "resolve_api_key",
    "sha256_file",
    "validate_api_url",
    "validate_geotiff",
    "write_metadata",
]
