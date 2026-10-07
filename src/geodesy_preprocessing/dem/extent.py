"""Geographic extents for DEM acquisition.

功能概述:
    从 KML 或显式经纬度生成 WGS84 下载范围，并提供缓冲和面积估算。

函数说明:
    ``BoundingBox`` 保存并校验范围；``bbox_from_kml`` 读取 KML；
    ``bbox_from_config`` 读取经纬度；``expand_bbox_km`` 外扩范围；
    ``estimate_bbox_area_km2`` 估算请求面积。
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BoundingBox:
    """Validated WGS84 longitude/latitude bounds."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "BoundingBox":
        """Validate finite coordinates, geographic limits, and ordering."""

        for name, value, low, high in (
            ("west", self.west, -180.0, 180.0),
            ("east", self.east, -180.0, 180.0),
            ("south", self.south, -90.0, 90.0),
            ("north", self.north, -90.0, 90.0),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
            if not low <= value <= high:
                raise ValueError(f"{name}={value} is outside [{low}, {high}]")
        if self.west >= self.east:
            raise ValueError(
                f"west must be smaller than east: {self.west} >= {self.east}"
            )
        if self.south >= self.north:
            raise ValueError(
                f"south must be smaller than north: {self.south} >= {self.north}"
            )
        return self

    @property
    def center_latitude(self) -> float:
        """Return the midpoint latitude."""

        return 0.5 * (self.south + self.north)

    def as_dict(self) -> dict[str, float]:
        """Return JSON-ready named coordinates."""

        return {
            "west": self.west,
            "south": self.south,
            "east": self.east,
            "north": self.north,
        }


def bbox_from_kml(
    kml_path: str | Path, placemark_name: str | None = None
) -> BoundingBox:
    """Return the union extent, or exactly one named Placemark extent."""

    path = Path(kml_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"KML file not found: {path}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise ValueError(f"invalid KML/XML file: {path}: {error}") from error

    if placemark_name:
        matches = []
        for placemark in root.iter():
            if placemark.tag.split("}")[-1] != "Placemark":
                continue
            name = next(
                (
                    child.text.strip()
                    for child in placemark
                    if child.tag.split("}")[-1] == "name" and child.text
                ),
                "",
            )
            if name == placemark_name:
                matches.append(placemark)
        if len(matches) != 1:
            raise ValueError(
                f"KML placemark {placemark_name!r} matched {len(matches)} entries; "
                "exactly one is required"
            )
        coordinate_elements = [
            item
            for item in matches[0].iter()
            if item.tag.split("}")[-1] == "coordinates" and item.text
        ]
    else:
        coordinate_elements = [
            item
            for item in root.iter()
            if item.tag.split("}")[-1] == "coordinates" and item.text
        ]

    longitudes: list[float] = []
    latitudes: list[float] = []
    for element in coordinate_elements:
        for token in element.text.replace("\n", " ").replace("\t", " ").split():
            parts = token.split(",")
            if len(parts) < 2:
                continue
            try:
                longitude, latitude = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if math.isfinite(longitude) and math.isfinite(latitude):
                longitudes.append(longitude)
                latitudes.append(latitude)
    if not longitudes:
        raise ValueError(f"no valid <coordinates> values found in KML file: {path}")
    return BoundingBox(
        min(longitudes), min(latitudes), max(longitudes), max(latitudes)
    )


def bbox_from_config(cfg: Mapping[str, str]) -> BoundingBox:
    """Build bounds from the four explicit coordinate fields."""

    required = ("west", "south", "east", "north")
    missing = [name for name in required if not str(cfg.get(name, "")).strip()]
    if missing:
        raise ValueError(
            "input_mode=bbox requires west, south, east, north; missing: "
            + ", ".join(missing)
        )
    try:
        return BoundingBox(*(float(str(cfg[name]).strip()) for name in required))
    except ValueError as error:
        if "must be" in str(error) or "outside" in str(error):
            raise
        raise ValueError("west, south, east, and north must be numbers") from error


def expand_bbox_km(bbox: BoundingBox, buffer_km: float) -> BoundingBox:
    """Expand bounds by an approximate distance in every direction."""

    if buffer_km < 0:
        raise ValueError("buffer_km must be >= 0")
    if buffer_km == 0:
        return bbox
    latitude_degree_km = 111.32
    longitude_degree_km = latitude_degree_km * max(
        abs(math.cos(math.radians(bbox.center_latitude))), 1e-6
    )
    latitude_delta = buffer_km / latitude_degree_km
    longitude_delta = buffer_km / longitude_degree_km
    return BoundingBox(
        max(-180.0, bbox.west - longitude_delta),
        max(-90.0, bbox.south - latitude_delta),
        min(180.0, bbox.east + longitude_delta),
        min(90.0, bbox.north + latitude_delta),
    )


def estimate_bbox_area_km2(bbox: BoundingBox) -> float:
    """Estimate a latitude/longitude rectangle area on a sphere."""

    earth_radius_km = 6371.0088
    return (
        earth_radius_km**2
        * abs(math.radians(bbox.east) - math.radians(bbox.west))
        * abs(math.sin(math.radians(bbox.north)) - math.sin(math.radians(bbox.south)))
    )


__all__ = [
    "BoundingBox",
    "bbox_from_config",
    "bbox_from_kml",
    "estimate_bbox_area_km2",
    "expand_bbox_km",
]
