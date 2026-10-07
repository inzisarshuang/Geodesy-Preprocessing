"""Backend-neutral Sentinel-1 burst-table domain values and numeric input.

功能概述:
    定义一基 burst 选择区间、原始 burst 编号行，并规范化 GAMMA
    ``S1_burstloc`` 派生的九列数值输入。

函数说明:
    ``BurstSelection`` 表示一个子带内的连续 burst 选择；
    ``BurstNumberRow`` 表示一个带真实 IW 标签的编号表行；
    ``read_iw_file`` 将可重排的有限数值输入规范为 N×9 数组；
    ``load_roi`` 读取 TXT、KML 或 GeoJSON 多边形；
    ``full_burst_roi`` 生成覆盖全部可用子带的包围盒；
    ``select_bursts`` 按正面积相交选择一基 burst 区间；
    ``read_burst_number_table`` 读取带真实 IW 键的原始编号表；
    ``build_reference_burst_table`` 计算所选区间对应的参考编号行；
    ``write_burst_itab`` 与 ``write_reference_burst_table`` 原子写入兼容文本。
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def _validate_swath(swath: int) -> None:
    if not isinstance(swath, int) or isinstance(swath, bool) or swath not in (1, 2, 3):
        raise ValueError("swath must be an integer from 1 through 3")


@dataclass(frozen=True)
class BurstSelection:
    """One-based inclusive burst interval for one IW swath."""

    swath: int
    first_burst: int
    last_burst: int

    def __post_init__(self) -> None:
        _validate_swath(self.swath)
        if self.first_burst < 1 or self.last_burst < self.first_burst:
            raise ValueError(
                "burst interval must satisfy 1 <= first_burst <= last_burst"
            )


@dataclass(frozen=True)
class BurstNumberRow:
    """Source or calculated burst-number values for one IW swath."""

    swath: int
    number_of_bursts: int
    first_burst: float
    last_burst: float

    def __post_init__(self) -> None:
        _validate_swath(self.swath)
        if self.number_of_bursts < 1:
            raise ValueError("number_of_bursts must be positive")
        if not math.isfinite(self.first_burst) or not math.isfinite(self.last_burst):
            raise ValueError("first_burst and last_burst must be finite")


def read_iw_file(path: str | Path) -> np.ndarray:
    """Read finite numeric values and normalize them to N rows by nine columns."""

    source = Path(path)
    values = np.asarray(np.loadtxt(source), dtype=float)
    if values.size == 0 or values.size % 9:
        raise ValueError(f"{source} value count must be positive and divisible by 9")
    rows = values.reshape((-1, 9))
    if not np.isfinite(rows).all():
        raise ValueError(f"{source} must contain only finite values")
    return rows


def _validate_roi(geometry: BaseGeometry, source: object) -> BaseGeometry:
    if geometry.is_empty:
        raise ValueError(f"ROI is empty: {source}")
    if not geometry.is_valid:
        raise ValueError(f"ROI must be valid: {source}")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"ROI must be Polygon or MultiPolygon: {source}")
    return geometry


def _load_txt_roi(path: Path) -> Polygon:
    values = np.asarray(np.loadtxt(path), dtype=float)
    if values.ndim == 1:
        values = values.reshape((1, -1))
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError(f"TXT ROI must contain exactly two lon/lat columns: {path}")
    if not np.isfinite(values).all():
        raise ValueError(f"ROI coordinates must be finite: {path}")
    return Polygon(values)


def _load_kml_roi(path: Path) -> Polygon:
    root = ET.parse(path).getroot()
    polygon = root.find(".//{*}Polygon")
    if polygon is None:
        raise ValueError(f"No Polygon found in KML file: {path}")
    coordinates = polygon.find(".//{*}coordinates")
    if coordinates is None or not coordinates.text:
        raise ValueError(f"KML Polygon has no coordinates: {path}")
    points: list[tuple[float, float]] = []
    for token in coordinates.text.split():
        fields = token.split(",")
        if len(fields) < 2:
            raise ValueError(f"Unexpected KML coordinate format: {path}")
        points.append((float(fields[0]), float(fields[1])))
    return Polygon(points)


def _geojson_polygons(document: object) -> list[Polygon]:
    if not isinstance(document, dict):
        return []
    geometry_type = document.get("type")
    if geometry_type == "FeatureCollection":
        polygons: list[Polygon] = []
        for feature in document.get("features", []):
            polygons.extend(_geojson_polygons(feature))
        return polygons
    if geometry_type == "Feature":
        return _geojson_polygons(document.get("geometry"))
    coordinates = document.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, list) and coordinates:
        return [Polygon(coordinates[0], coordinates[1:])]
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        return [
            Polygon(polygon[0], polygon[1:])
            for polygon in coordinates
            if isinstance(polygon, list) and polygon
        ]
    return []


def _load_geojson_roi(path: Path) -> BaseGeometry:
    with path.open("r", encoding="utf-8") as stream:
        polygons = _geojson_polygons(json.load(stream))
    if not polygons:
        raise ValueError(f"No Polygon found in GeoJSON file: {path}")
    if len(polygons) == 1:
        return polygons[0]
    return unary_union(polygons)


def load_roi(path: str | Path) -> BaseGeometry:
    """Load and validate a TXT, KML, or GeoJSON polygonal ROI."""

    source = Path(path)
    extension = source.suffix.lower()
    if extension == ".txt":
        geometry = _load_txt_roi(source)
    elif extension == ".kml":
        geometry = _load_kml_roi(source)
    elif extension in {".geojson", ".json"}:
        geometry = _load_geojson_roi(source)
    else:
        raise ValueError("ROI file must use txt, kml, geojson, or json format")
    return _validate_roi(geometry, source)


def full_burst_roi(iw_files: Mapping[int, str | Path]) -> BaseGeometry:
    """Return the lon/lat bounding polygon across every available IW file."""

    if not iw_files:
        raise ValueError("at least one IW file is required")
    longitudes: list[np.ndarray] = []
    latitudes: list[np.ndarray] = []
    for swath, path in sorted(iw_files.items()):
        _validate_swath(swath)
        rows = read_iw_file(path)
        longitudes.append(rows[:, [2, 4, 6, 8]].reshape(-1))
        latitudes.append(rows[:, [1, 3, 5, 7]].reshape(-1))
    longitude = np.concatenate(longitudes)
    latitude = np.concatenate(latitudes)
    return box(
        float(np.min(longitude)),
        float(np.min(latitude)),
        float(np.max(longitude)),
        float(np.max(latitude)),
    )


def select_bursts(
    iw_files: Mapping[int, str | Path],
    roi: BaseGeometry,
) -> tuple[BurstSelection, ...]:
    """Select one-based burst intervals whose polygons overlap the ROI by area."""

    _validate_roi(roi, "provided geometry")
    selections: list[BurstSelection] = []
    for swath, path in sorted(iw_files.items()):
        _validate_swath(swath)
        selected: list[int] = []
        for index, row in enumerate(read_iw_file(path), start=1):
            latitudes = row[[1, 3, 5, 7]]
            longitudes = row[[2, 4, 6, 8]]
            polygon = Polygon(zip(longitudes.tolist(), latitudes.tolist()))
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError(f"invalid burst polygon in swath {swath}, burst {index}")
            intersection = polygon.intersection(roi)
            if not intersection.is_empty and intersection.area > 0:
                selected.append(index)
        if selected:
            selections.append(BurstSelection(swath, min(selected), max(selected)))
    if not selections:
        raise ValueError("no bursts overlap the ROI")
    return tuple(selections)


_NUMBER_PATTERN = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_IW_PATTERN = re.compile(r"iw[_\s]?(\d+)", flags=re.IGNORECASE)


def read_burst_number_table(path: str | Path) -> Mapping[int, BurstNumberRow]:
    """Read structured IW fields or one unambiguous legacy numeric 3×3 table."""

    source = Path(path)
    text = source.read_text(encoding="utf-8")
    fields: dict[int, dict[str, float]] = {}
    for line in text.splitlines():
        swath_match = _IW_PATTERN.search(line)
        if swath_match is None:
            continue
        swath = int(swath_match.group(1))
        _validate_swath(swath)
        field: str | None = None
        if re.search(r"number\s*_?of\s*_?bursts", line, flags=re.IGNORECASE):
            field = "number"
        elif re.search(r"first\s*_?burst", line, flags=re.IGNORECASE):
            field = "first"
        elif re.search(r"last\s*_?burst", line, flags=re.IGNORECASE):
            field = "last"
        if field is None:
            continue
        numbers = _NUMBER_PATTERN.findall(line)
        if not numbers:
            raise ValueError(f"missing numeric value for swath {swath} {field}")
        fields.setdefault(swath, {})[field] = float(numbers[-1])

    if fields:
        rows: dict[int, BurstNumberRow] = {}
        required = {"number", "first", "last"}
        for swath in sorted(fields):
            values = fields[swath]
            if set(values) != required:
                raise ValueError(f"incomplete burst-number fields for swath {swath}")
            count_value = values["number"]
            if not count_value.is_integer():
                raise ValueError(f"number_of_bursts for swath {swath} must be an integer")
            rows[swath] = BurstNumberRow(
                swath,
                int(count_value),
                values["first"],
                values["last"],
            )
        return rows

    numeric_tokens = _NUMBER_PATTERN.findall(text)
    if len(numeric_tokens) != 9:
        raise ValueError(
            "legacy numeric burst-number table must contain exactly 9 values"
        )
    numeric = np.asarray([float(token) for token in numeric_tokens], dtype=float)
    legacy_rows = numeric.reshape((3, 3), order="F").T
    return {
        swath: BurstNumberRow(
            swath,
            int(row[0]),
            float(row[1]),
            float(row[2]),
        )
        for swath, row in enumerate(legacy_rows, start=1)
    }


def build_reference_burst_table(
    selections: Sequence[BurstSelection],
    source_rows: Mapping[int, BurstNumberRow],
) -> tuple[BurstNumberRow, ...]:
    """Calculate selected reference rows by explicit swath key."""

    result: list[BurstNumberRow] = []
    for selection in sorted(selections, key=lambda value: value.swath):
        try:
            source = source_rows[selection.swath]
        except KeyError as error:
            raise ValueError(
                f"source burst-number table has no row for swath {selection.swath}"
            ) from error
        count = selection.last_burst - selection.first_burst + 1
        first = source.first_burst + selection.first_burst - 1
        last = first + count - 1
        result.append(
            BurstNumberRow(selection.swath, count, float(first), float(last))
        )
    return tuple(result)


def _write_text_atomic(path: Path, payload: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_burst_itab(
    selections: Sequence[BurstSelection], path: str | Path
) -> Path:
    """Atomically write legacy-compatible one-based selection rows."""

    payload = "".join(
        f"{selection.swath} {selection.first_burst} {selection.last_burst}\n"
        for selection in sorted(selections, key=lambda value: value.swath)
    )
    return _write_text_atomic(Path(path), payload)


def write_reference_burst_table(
    rows: Sequence[BurstNumberRow], path: str | Path
) -> Path:
    """Atomically write a legacy-compatible reference burst-number table."""

    parts = ["zipfile:    ref.zip\n"]
    for row in sorted(rows, key=lambda value: value.swath):
        parts.extend(
            (
                f"iw{row.swath}_number_of_bursts: {row.number_of_bursts}\n",
                f"iw{row.swath}_first_burst:      {row.first_burst:.6f}\n",
                f"iw{row.swath}_last_burst:       {row.last_burst:.6f}\n",
            )
        )
    return _write_text_atomic(Path(path), "".join(parts))


__all__ = [
    "BurstNumberRow",
    "BurstSelection",
    "build_reference_burst_table",
    "full_burst_roi",
    "load_roi",
    "read_burst_number_table",
    "read_iw_file",
    "select_bursts",
    "write_burst_itab",
    "write_reference_burst_table",
]
