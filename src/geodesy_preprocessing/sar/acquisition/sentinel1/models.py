"""Sentinel-1 获取阶段的不可变后端中立数据模型。

功能概述:
    描述严格项目 case 和从外部提供方规范化后的 Sentinel-1 产品；模型不保存账号、
    密码、Cookie 或令牌。

函数说明:
    ``Sentinel1ProductType`` 区分 BURST、SLC 和 GRD；
    ``Sentinel1DownloadLayout`` 区分逐轨子目录与单轨扁平下载；
    ``Sentinel1AcquisitionCase`` 保存查询与下载参数；``Sentinel1Product`` 保存单个
    不含凭据的提供方产品元数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Sentinel1ProductType(str, Enum):
    """Sentinel-1 products accepted by the acquisition workflow."""

    BURST = "BURST"
    SLC = "SLC"
    GRD = "GRD"


class Sentinel1DownloadLayout(str, Enum):
    """Physical layout used below one acquisition case's download directory."""

    ORBIT_SUBDIRECTORIES = "orbit_subdirectories"
    FLAT = "flat"


@dataclass(frozen=True)
class Sentinel1AcquisitionCase:
    """One project-local Sentinel-1 query, download, and optional merge case."""

    name: str
    project_root: Path
    kml_file: Path | None
    bbox: tuple[float, float, float, float] | None
    start_date: str
    end_date: str
    product_type: Sentinel1ProductType
    beam_mode: str
    polarizations: tuple[str, ...] | None
    provider: str
    tracks: tuple[int, ...] | None
    orbit_direction: str | None
    coverage_threshold: float
    query_dir: Path
    download_dir: Path
    download_threads: int
    max_retries: int
    retry_delay_seconds: int
    keep_burst_files: bool
    download_layout: Sentinel1DownloadLayout = (
        Sentinel1DownloadLayout.ORBIT_SUBDIRECTORIES
    )


@dataclass(frozen=True)
class Sentinel1Product:
    """One normalized Sentinel-1 provider product without authentication data."""

    product_id: str
    product_type: Sentinel1ProductType
    acquisition_date: str
    start_time: str
    end_time: str
    relative_orbit: int
    absolute_orbit: int
    orbit_direction: str
    beam_mode: str
    polarizations: tuple[str, ...]
    footprint_wkt: str
    file_name: str
    size_bytes: int
    url: str
    swath: str | None = None
    burst_index: int | None = None
    relative_burst_id: int | None = None
    full_burst_id: str | None = None
    merge_group: str | None = None
    roi_match: bool = True
    supplemental: bool = False


__all__ = [
    "Sentinel1AcquisitionCase",
    "Sentinel1DownloadLayout",
    "Sentinel1Product",
    "Sentinel1ProductType",
]
