"""NISAR 获取阶段的不可变后端中立数据模型。

功能概述:
    描述查询配置、频率模式以及从外部提供方规范化后的单个 NISAR 产品。

函数说明:
    ``NisarFrequencyMode`` 区分仅主频和显式双频；``NisarProductType`` 明确区分
    RSLC 与 GSLC；``NisarSearchCase`` 保存查询 case；``NisarGranule`` 保存不含凭据的
    规范化产品元数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class NisarFrequencyMode(str, Enum):
    """Requested NISAR frequency scope without implying frequency ordering."""

    MAIN_ONLY = "main_only"
    MAIN_PLUS_AUXILIARY = "main_plus_auxiliary"


class NisarProductType(str, Enum):
    """NISAR science product accepted by the acquisition workflow."""

    RSLC = "RSLC"
    GSLC = "GSLC"


@dataclass(frozen=True)
class NisarSearchCase:
    """One project-local NISAR search and download selection."""

    name: str
    project_root: Path
    kml_file: Path
    start_date: str
    end_date: str
    product_type: NisarProductType
    band: str
    polarizations: tuple[str, ...]
    provider: str
    frequency_mode: NisarFrequencyMode
    tracks: tuple[int, ...] | None
    frames: tuple[int, ...] | None
    orbit_direction: str | None
    query_dir: Path
    download_dir: Path


@dataclass(frozen=True)
class NisarGranule:
    """One normalized NISAR product returned by a search provider."""

    granule_id: str
    product_type: NisarProductType
    acquisition_date: str
    start_time: str
    end_time: str
    track: int
    frame: int
    orbit_direction: str
    footprint_wkt: str
    size_bytes: int
    url: str
    frequencies: tuple[str, ...]
    polarizations: tuple[str, ...]
    checksum: str | None = None


__all__ = [
    "NisarFrequencyMode",
    "NisarGranule",
    "NisarProductType",
    "NisarSearchCase",
]
