"""NISAR GSLC 项目栈配置和只读计划模型。

功能概述:
    定义项目 case、统一像元窗口、单个日期/极化资产以及完整 GSLC 栈计划。

函数说明:
    ``NisarGslcStackCase`` 保存用户配置；``GslcPixelWindow`` 保存外扩后的整数窗口；
    ``NisarGslcAssetPlan`` 保存单个复数数据集；``NisarGslcStackPlan`` 保存通过身份、
    网格和覆盖检查后的完整计划。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5 import (
    NisarGslcChannel,
    NisarGslcGrid,
)


@dataclass(frozen=True)
class NisarGslcStackCase:
    """One project-local native GSLC stack preparation case."""

    name: str
    project_root: Path
    source_manifest: Path
    kml_file: Path
    kml_placemark: str | None
    buffer_km: float
    band: str
    frequency: str
    polarizations: tuple[str, ...]
    reference_date: str
    output_dir: Path


@dataclass(frozen=True)
class GslcPixelWindow:
    """Half-open row/column window rounded outward on a GSLC grid."""

    row_start: int
    row_stop: int
    column_start: int
    column_stop: int

    @property
    def shape(self) -> tuple[int, int]:
        return self.row_stop - self.row_start, self.column_stop - self.column_start


@dataclass(frozen=True)
class NisarGslcAssetPlan:
    """One selected date and polarization dataset on the common grid."""

    date: str
    polarization: str
    source_file: Path
    source_sha256: str
    channel: NisarGslcChannel
    grid: NisarGslcGrid
    window: GslcPixelWindow


@dataclass(frozen=True)
class NisarGslcStackPlan:
    """Complete immutable identity, grid, crop, and asset plan for one stack."""

    case: NisarGslcStackCase
    product_type: str
    product_specification_version: str
    track: int
    frame: int
    orbit_direction: str
    dates: tuple[str, ...]
    reference_date: str
    selected_polarizations: tuple[str, ...]
    grid: NisarGslcGrid
    window: GslcPixelWindow
    roi: BaseGeometry
    buffered_roi: BaseGeometry
    roi_coverage: tuple[tuple[str, float], ...]
    buffered_coverage: tuple[tuple[str, float], ...]
    assets: tuple[NisarGslcAssetPlan, ...]
    existing_complete: bool


__all__ = [
    "GslcPixelWindow",
    "NisarGslcAssetPlan",
    "NisarGslcStackCase",
    "NisarGslcStackPlan",
]
