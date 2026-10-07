"""Strict readers for GAMMA map and radar parameter files.

功能概述:
    从 GAMMA 文本参数文件读取裁剪所需字段，并对缺失或非法数值给出明确错误。

函数说明:
    ``read_gamma_map_grid`` 读取 EQA/UTM 地图网格；
    ``read_gamma_radar_grid`` 读取 MLI、SLC 或 RSLC 雷达网格。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pyproj import CRS
from rasterio import Affine

from .models import GammaMapGrid, GammaRadarGrid


def _fields(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _required(values: dict[str, str], field: str, path: Path) -> str:
    value = values.get(field, "").split()
    if not value:
        raise ValueError(f"{path}: missing required field {field}")
    return value[0]


def _integer(values: dict[str, str], field: str, path: Path) -> int:
    try:
        value = int(_required(values, field, path))
    except ValueError as error:
        if "missing required" in str(error):
            raise
        raise ValueError(f"{path}: field {field} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{path}: field {field} must be positive")
    return value


def _number(values: dict[str, str], field: str, path: Path) -> float:
    try:
        return float(_required(values, field, path))
    except ValueError as error:
        if "missing required" in str(error):
            raise
        raise ValueError(f"{path}: field {field} must be numeric") from error


def read_gamma_map_grid(path: Path) -> GammaMapGrid:
    """Read a GAMMA EQA or UTM DEM/MAP parameter grid."""

    source = Path(path)
    values = _fields(source)
    projection = _required(values, "DEM_projection", source).upper()
    if projection not in {"EQA", "UTM"}:
        raise ValueError(f"{source}: unsupported DEM_projection {projection!r}")
    try:
        crs = CRS.from_epsg(_integer(values, "EPSG", source))
    except Exception as error:
        if isinstance(error, ValueError) and "missing required" in str(error):
            raise
        raise ValueError(f"{source}: field EPSG does not identify a valid CRS") from error
    width = _integer(values, "width", source)
    nlines = _integer(values, "nlines", source)
    if projection == "EQA":
        x_name, y_name = "corner_lon", "corner_lat"
        x_post_name, y_post_name = "post_lon", "post_lat"
    else:
        x_name, y_name = "corner_east", "corner_north"
        x_post_name, y_post_name = "post_east", "post_north"
    x_center = _number(values, x_name, source)
    y_center = _number(values, y_name, source)
    x_post = _number(values, x_post_name, source)
    y_post = _number(values, y_post_name, source)
    if x_post == 0.0 or y_post == 0.0:
        raise ValueError(f"{source}: map pixel posting must be non-zero")
    transform = Affine(
        x_post,
        0.0,
        x_center - x_post / 2.0,
        0.0,
        y_post,
        y_center - y_post / 2.0,
    )
    return GammaMapGrid(source, projection, crs, width, nlines, transform)


def read_gamma_radar_grid(path: Path) -> GammaRadarGrid:
    """Read dimensions, looks and storage metadata from a GAMMA image parameter file."""

    source = Path(path)
    values = _fields(source)
    width_field = "width" if "width" in values and "range_samples" not in values else "range_samples"
    lines_field = "nlines" if "nlines" in values and "azimuth_lines" not in values else "azimuth_lines"
    polarization = values.get("polarization", "").split()
    date_fields = values.get("date", "").split()
    acquisition_date: str | None = None
    if date_fields:
        if len(date_fields) < 3:
            raise ValueError(f"{source}: field date must begin with year month day")
        try:
            acquisition_date = datetime(
                int(date_fields[0]), int(date_fields[1]), int(date_fields[2])
            ).strftime("%Y%m%d")
        except ValueError as error:
            raise ValueError(f"{source}: field date is invalid") from error
    return GammaRadarGrid(
        parameter_path=source,
        width=_integer(values, width_field, source),
        nlines=_integer(values, lines_field, source),
        range_looks=_integer(values, "range_looks", source),
        azimuth_looks=_integer(values, "azimuth_looks", source),
        image_format=_required(values, "image_format", source).upper(),
        polarization=polarization[0].upper() if polarization else None,
        acquisition_date=acquisition_date,
    )


__all__ = ["read_gamma_map_grid", "read_gamma_radar_grid"]
