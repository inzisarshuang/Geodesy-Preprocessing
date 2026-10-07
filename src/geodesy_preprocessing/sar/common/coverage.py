"""SAR 足迹的公共等面积覆盖率计算。

功能概述:
    在研究区中心的局部等面积坐标系计算 Polygon/MultiPolygon 覆盖率，并正确处理
    跨 180° 经线的局部研究区。

函数说明:
    ``coverage_fraction`` 返回一个足迹覆盖研究区的面积比例；私有辅助函数供各传感器
    的轨道汇总复用。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from pyproj import CRS, Transformer
from shapely import segmentize
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


_MAX_GEOGRAPHIC_SEGMENT_DEGREES = 0.1


def _validate_polygon(geometry: BaseGeometry, label: str) -> None:
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"{label} must be a Polygon or MultiPolygon")
    if geometry.is_empty:
        raise ValueError(f"{label} must not be empty")
    if not geometry.is_valid:
        raise ValueError(f"{label} must be valid")


def _longitudes(geometry: BaseGeometry) -> Iterable[float]:
    polygons = (geometry,) if isinstance(geometry, Polygon) else geometry.geoms
    for polygon in polygons:
        for longitude, *_ in polygon.exterior.coords:
            yield float(longitude)


def _longitude_reference(geometry: BaseGeometry) -> float:
    values = tuple(_longitudes(geometry))
    sine = sum(math.sin(math.radians(value)) for value in values)
    cosine = sum(math.cos(math.radians(value)) for value in values)
    if math.isclose(sine, 0.0, abs_tol=1e-15) and math.isclose(
        cosine, 0.0, abs_tol=1e-15
    ):
        return float(values[0])
    return math.degrees(math.atan2(sine, cosine))


def _unwrap_ring(coordinates: Iterable[Sequence[float]], reference: float) -> list[tuple]:
    result: list[tuple] = []
    previous = reference
    for coordinate in coordinates:
        longitude = float(coordinate[0])
        latitude = float(coordinate[1])
        longitude += 360.0 * round((previous - longitude) / 360.0)
        previous = longitude
        result.append((longitude, latitude, *coordinate[2:]))
    return result


def _unwrap_polygon(polygon: Polygon, reference: float) -> Polygon:
    return Polygon(
        _unwrap_ring(polygon.exterior.coords, reference),
        [_unwrap_ring(interior.coords, reference) for interior in polygon.interiors],
    )


def _unwrap_geometry(geometry: BaseGeometry, reference: float) -> BaseGeometry:
    if isinstance(geometry, Polygon):
        return _unwrap_polygon(geometry, reference)
    return MultiPolygon(tuple(_unwrap_polygon(part, reference) for part in geometry.geoms))


def _projection(aoi: BaseGeometry, reference: float) -> Transformer:
    centroid = aoi.centroid
    longitude = ((reference + 180.0) % 360.0) - 180.0
    latitude = max(-89.999, min(89.999, float(centroid.y)))
    equal_area = CRS.from_proj4(
        f"+proj=laea +lat_0={latitude:.12f} +lon_0={longitude:.12f} "
        "+datum=WGS84 +units=m +no_defs"
    )
    return Transformer.from_crs(
        CRS.from_epsg(4326), equal_area, always_xy=True, force_over=True
    )


def _prepare_aoi(aoi: BaseGeometry) -> tuple[float, BaseGeometry, Transformer]:
    _validate_polygon(aoi, "AOI")
    reference = _longitude_reference(aoi)
    unwrapped = _unwrap_geometry(aoi, reference)
    _validate_polygon(unwrapped, "AOI")
    transformer = _projection(unwrapped, reference)
    projected = transform(
        transformer.transform,
        segmentize(unwrapped, _MAX_GEOGRAPHIC_SEGMENT_DEGREES),
    )
    if projected.area <= 0.0:
        raise ValueError("AOI projected area must be positive")
    return reference, projected, transformer


def _project_footprint(
    footprint: BaseGeometry,
    reference: float,
    transformer: Transformer,
    label: str = "footprint",
) -> BaseGeometry:
    _validate_polygon(footprint, label)
    unwrapped = _unwrap_geometry(footprint, reference)
    _validate_polygon(unwrapped, label)
    return transform(
        transformer.transform,
        segmentize(unwrapped, _MAX_GEOGRAPHIC_SEGMENT_DEGREES),
    )


def _fraction(projected_aoi: BaseGeometry, projected_footprint: BaseGeometry) -> float:
    value = projected_aoi.intersection(projected_footprint).area / projected_aoi.area
    return max(0.0, min(1.0, float(value)))


def coverage_fraction(aoi: BaseGeometry, footprint: BaseGeometry) -> float:
    """Return the AOI fraction covered by one footprint in a local equal-area CRS."""

    reference, projected_aoi, transformer = _prepare_aoi(aoi)
    projected_footprint = _project_footprint(footprint, reference, transformer)
    return _fraction(projected_aoi, projected_footprint)


__all__ = ["coverage_fraction"]
