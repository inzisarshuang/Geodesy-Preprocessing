"""Immutable grid models for GAMMA radar-stack subsetting.

功能概述:
    描述 GAMMA 地图网格和雷达网格中裁剪所需的最小参数集合。

函数说明:
    ``GammaMapGrid`` 保存 CRS、地图尺寸和像元边界变换；
    ``GammaRadarGrid`` 保存雷达图像尺寸、视数、格式和极化。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyproj import CRS
from rasterio import Affine


@dataclass(frozen=True)
class GammaMapGrid:
    """A map-projected GAMMA raster grid."""

    parameter_path: Path
    projection: str
    crs: CRS
    width: int
    nlines: int
    transform: Affine


@dataclass(frozen=True)
class GammaRadarGrid:
    """A GAMMA radar image grid and storage format."""

    parameter_path: Path
    width: int
    nlines: int
    range_looks: int
    azimuth_looks: int
    image_format: str
    polarization: str | None = None
    acquisition_date: str | None = None


__all__ = ["GammaMapGrid", "GammaRadarGrid"]
