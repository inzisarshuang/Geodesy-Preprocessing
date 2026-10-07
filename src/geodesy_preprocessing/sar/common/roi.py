"""Backend-neutral KML region-of-interest geometry.

功能概述:
    读取 WGS84 KML 中的 Polygon/MultiGeometry，并在当地米制坐标系中生成缓冲区。

函数说明:
    ``load_kml_roi`` 读取全部或指定 Placemark 的多边形；
    ``buffer_wgs84_geometry`` 使用当地 UTM 坐标系按米向外缓冲。
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union


def _coordinates(element: ET.Element, source: Path) -> list[tuple[float, float]]:
    if not element.text:
        raise ValueError(f"KML Polygon has no coordinates: {source}")
    points: list[tuple[float, float]] = []
    for token in element.text.split():
        fields = token.split(",")
        if len(fields) < 2:
            raise ValueError(f"unexpected KML coordinate format: {source}")
        try:
            longitude, latitude = float(fields[0]), float(fields[1])
        except ValueError as error:
            raise ValueError(f"non-numeric KML coordinate: {source}") from error
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise ValueError(f"non-finite KML coordinate: {source}")
        if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
            raise ValueError(f"KML coordinate outside WGS84 bounds: {source}")
        points.append((longitude, latitude))
    return points


def _polygon(element: ET.Element, source: Path) -> Polygon:
    outer = element.find("./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
    if outer is None:
        raise ValueError(f"KML Polygon has no outer boundary: {source}")
    holes = [
        _coordinates(coordinates, source)
        for coordinates in element.findall(
            "./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"
        )
    ]
    return Polygon(_coordinates(outer, source), holes)


def _validate_wgs84_polygon(geometry: BaseGeometry, source: object) -> BaseGeometry:
    if geometry.is_empty:
        raise ValueError(f"KML Polygon geometry is empty: {source}")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"KML ROI must contain Polygon geometry: {source}")
    if not geometry.is_valid:
        raise ValueError(f"KML Polygon geometry is invalid: {source}")
    west, south, east, north = geometry.bounds
    if east - west > 180.0:
        raise ValueError(f"KML geometry crossing the antimeridian is unsupported: {source}")
    if not (-180.0 <= west <= east <= 180.0 and -90.0 <= south <= north <= 90.0):
        raise ValueError(f"KML geometry lies outside WGS84 bounds: {source}")
    return geometry


def load_kml_roi(path: Path, placemark: str | None = None) -> BaseGeometry:
    """Load and merge polygonal WGS84 geometry from a KML document."""

    source = Path(path)
    root = ET.parse(source).getroot()
    placemarks = root.findall(".//{*}Placemark")
    if placemark is not None:
        placemarks = [
            item
            for item in placemarks
            if (item.findtext("./{*}name") or "").strip() == placemark
        ]
        if not placemarks:
            raise ValueError(f"KML Placemark {placemark!r} is missing: {source}")
    containers: list[ET.Element] = placemarks or [root]
    polygons = [
        _polygon(polygon, source)
        for container in containers
        for polygon in container.findall(".//{*}Polygon")
    ]
    if not polygons:
        raise ValueError(f"no Polygon found in KML file: {source}")
    return _validate_wgs84_polygon(unary_union(polygons), source)


def buffer_wgs84_geometry(
    geometry: BaseGeometry,
    distance_m: float,
) -> BaseGeometry:
    """Buffer WGS84 polygonal geometry in a local UTM CRS and return WGS84."""

    validated = _validate_wgs84_polygon(geometry, "provided geometry")
    if not isinstance(distance_m, (int, float)) or isinstance(distance_m, bool):
        raise TypeError("buffer distance must be numeric metres")
    distance = float(distance_m)
    if not math.isfinite(distance) or distance < 0.0:
        raise ValueError("buffer distance must be finite and non-negative")
    if distance == 0.0:
        return validated
    longitude, latitude = validated.centroid.x, validated.centroid.y
    if not -80.0 <= latitude <= 84.0:
        raise ValueError("local UTM buffering requires latitude from -80 to 84 degrees")
    zone = max(1, min(60, int((longitude + 180.0) // 6.0) + 1))
    metric = CRS.from_epsg((32600 if latitude >= 0.0 else 32700) + zone)
    forward = Transformer.from_crs(4326, metric, always_xy=True)
    reverse = Transformer.from_crs(metric, 4326, always_xy=True)
    buffered = transform(forward.transform, validated).buffer(distance)
    return _validate_wgs84_polygon(
        transform(reverse.transform, buffered), "buffered geometry"
    )


__all__ = ["buffer_wgs84_geometry", "load_kml_roi"]
