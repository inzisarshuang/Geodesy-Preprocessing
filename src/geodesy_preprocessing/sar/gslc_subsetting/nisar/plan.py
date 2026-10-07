"""NISAR GSLC 身份、统一网格和 KML 外扩窗口的只读计划。

功能概述:
    校验来源清单与 HDF5 身份、日期×极化完整性和完全一致的地图网格，再将 WGS84
    KML 外扩后投影到 GSLC CRS，向外取整为一个公共像元窗口；不读取复数影像数组。

函数说明:
    ``build_nisar_gslc_stack_plan`` 返回完整不可变计划，覆盖不足或任何网格差异都会在
    创建输出前停止。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import box
from shapely.ops import transform

from geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5 import (
    NisarGslcChannel,
    NisarGslcGrid,
    inspect_nisar_gslc,
)
from geodesy_preprocessing.sar.common.roi import buffer_wgs84_geometry, load_kml_roi

from .models import (
    GslcPixelWindow,
    NisarGslcAssetPlan,
    NisarGslcStackCase,
    NisarGslcStackPlan,
)


_DATE = re.compile(r"(?P<year>\d{4})-?(?P<month>\d{2})-?(?P<day>\d{2})\Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _date(value: object, label: str) -> str:
    match = _DATE.fullmatch(str(value).strip())
    if match is None:
        raise ValueError(f"{label} must use YYYYMMDD or YYYY-MM-DD")
    return f"{match['year']}{match['month']}{match['day']}"


def _source_manifest(path: Path) -> tuple[dict[str, object], ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid NISAR source manifest: {path}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError(f"unsupported NISAR source manifest schema: {path}")
    products = document.get("products")
    if not isinstance(products, list) or not products:
        raise ValueError(f"NISAR source manifest has no products: {path}")
    if any(not isinstance(item, dict) for item in products):
        raise ValueError(f"NISAR source manifest products must be objects: {path}")
    return tuple(products)


def _required(record: dict[str, object], name: str, date: str) -> object:
    value = record.get(name)
    if value is None or value == "":
        raise ValueError(f"{date}: source manifest is missing {name}")
    return value


def _grid_value_equal(field: str, first: NisarGslcGrid, other: NisarGslcGrid) -> bool:
    left = getattr(first, field)
    right = getattr(other, field)
    if isinstance(left, float):
        tolerance = max(abs(left) * 1.0e-12, 1.0e-9)
        return math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
    return left == right


def _grid_footprint(grid: NisarGslcGrid):
    x0 = grid.x_start - 0.5 * grid.x_spacing
    x1 = grid.x_start + (grid.shape[1] - 0.5) * grid.x_spacing
    y0 = grid.y_start - 0.5 * grid.y_spacing
    y1 = grid.y_start + (grid.shape[0] - 0.5) * grid.y_spacing
    return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _coverage(footprint, geometry) -> float:
    if geometry.area <= 0.0:
        raise ValueError("projected KML area must be positive")
    value = footprint.intersection(geometry).area / geometry.area
    if value > 1.0 and value < 1.0 + 1.0e-12:
        return 1.0
    return max(0.0, min(1.0, float(value)))


def _axis_window(
    lower: float,
    upper: float,
    center_start: float,
    spacing: float,
    count: int,
) -> tuple[int, int]:
    edge_start = center_start - 0.5 * spacing
    first = (lower - edge_start) / spacing
    second = (upper - edge_start) / spacing
    low, high = min(first, second), max(first, second)
    tolerance = 1.0e-8
    start = math.floor(low + tolerance)
    stop = math.ceil(high - tolerance)
    if start < 0 or stop > count or start >= stop:
        raise ValueError("buffered AOI cannot be represented by a full in-bounds pixel window")
    return start, stop


def build_nisar_gslc_stack_plan(case: NisarGslcStackCase) -> NisarGslcStackPlan:
    """Validate source GSLC metadata and return one exact outward crop window."""

    if not isinstance(case, NisarGslcStackCase):
        raise TypeError("case must be NisarGslcStackCase")
    manifest_path = case.source_manifest.resolve(strict=True)
    if not manifest_path.is_file() or manifest_path.stat().st_size == 0:
        raise ValueError(f"source manifest must be a non-empty file: {manifest_path}")
    source_root = manifest_path.parent
    records = _source_manifest(manifest_path)

    dated_records: list[tuple[str, dict[str, object]]] = []
    seen_dates: set[str] = set()
    for index, record in enumerate(records, start=1):
        date = _date(_required(record, "acquisition_date", f"product {index}"), f"product {index} acquisition_date")
        if date in seen_dates:
            raise ValueError(f"duplicate GSLC acquisition date {date}")
        seen_dates.add(date)
        dated_records.append((date, record))
    dated_records.sort(key=lambda item: item[0])
    if case.reference_date not in seen_dates:
        raise ValueError(f"reference_date {case.reference_date} is absent from source products")

    expected_identity: tuple[int, int, str] | None = None
    expected_version: str | None = None
    common_grid: NisarGslcGrid | None = None
    assets_by_identity: dict[
        tuple[str, str], tuple[Path, str, NisarGslcChannel, NisarGslcGrid]
    ] = {}
    coverages_roi: list[tuple[str, float]] = []
    coverages_buffered: list[tuple[str, float]] = []
    inventories: list[tuple[str, NisarGslcGrid]] = []

    kml = case.kml_file.resolve(strict=True)
    if not kml.is_file() or kml.stat().st_size == 0:
        raise ValueError(f"KML ROI must be a non-empty file: {kml}")
    roi = load_kml_roi(kml, case.kml_placemark)
    buffered_roi = buffer_wgs84_geometry(roi, case.buffer_km * 1000.0)

    for date, record in dated_records:
        product_type = str(_required(record, "product_type", date)).upper()
        if product_type != "GSLC":
            raise ValueError(f"{date}: product_type must be GSLC, got {product_type}")
        identity = (
            int(_required(record, "track", date)),
            int(_required(record, "frame", date)),
            str(_required(record, "orbit_direction", date)).upper(),
        )
        if expected_identity is None:
            expected_identity = identity
        else:
            for field, left, right in zip(
                ("track", "frame", "orbit_direction"), expected_identity, identity, strict=True
            ):
                if left != right:
                    raise ValueError(f"{date}: {field} differs from the first GSLC product")
        filename = Path(str(_required(record, "file", date)))
        if filename.is_absolute():
            raise ValueError(f"{date}: source product file must be relative to source manifest")
        source = (source_root / filename).resolve(strict=True)
        if not source.is_relative_to(source_root) or not source.is_file():
            raise ValueError(f"{date}: source product lies outside the source directory")
        size = int(_required(record, "size_bytes", date))
        if source.stat().st_size != size:
            raise ValueError(f"{date}: source product size differs from source manifest")
        source_digest = _sha256(source)
        if source_digest != str(_required(record, "sha256", date)).lower():
            raise ValueError(f"{date}: source product sha256 differs from source manifest")

        inventory = inspect_nisar_gslc(source)
        comparisons = {
            "product_type": inventory.product_type,
            "granule_id": inventory.granule_id,
            "track": inventory.track,
            "frame": inventory.frame,
            "orbit_direction": inventory.orbit_direction,
        }
        for field, actual in comparisons.items():
            expected = _required(record, field, date)
            if str(actual).upper() != str(expected).upper():
                raise ValueError(f"{date}: HDF5 {field} differs from source manifest")
        inventory_date = _date(inventory.acquisition_start[:10], f"{date} HDF5 acquisition")
        if inventory_date != date:
            raise ValueError(f"{date}: HDF5 acquisition date is {inventory_date}")
        version = inventory.product_specification_version
        record_version = str(_required(record, "product_specification_version", date))
        if version != record_version:
            raise ValueError(f"{date}: HDF5 product_specification_version differs from source manifest")
        if expected_version is None:
            expected_version = version
        elif version != expected_version:
            raise ValueError(f"{date}: product_specification_version differs from first product")

        matching_grids = [
            grid
            for grid in inventory.grids
            if grid.band == case.band and grid.frequency == case.frequency
        ]
        if len(matching_grids) != 1:
            raise ValueError(f"{date}: missing unique {case.band}{case.frequency} GSLC grid")
        grid = matching_grids[0]
        if common_grid is None:
            common_grid = grid
        else:
            for field, label in (
                ("epsg", "EPSG"),
                ("shape", "shape"),
                ("x_spacing", "x_spacing"),
                ("y_spacing", "y_spacing"),
                ("x_start", "x_start"),
                ("y_start", "y_start"),
                ("center_frequency_hz", "center_frequency_hz"),
            ):
                if not _grid_value_equal(field, common_grid, grid):
                    raise ValueError(f"{date}: GSLC grid {label} differs from first product")
        inventories.append((date, grid))
        channel_map = {
            channel.polarization: channel
            for channel in inventory.channels
            if channel.band == case.band and channel.frequency == case.frequency
        }
        for polarization in case.polarizations:
            channel = channel_map.get(polarization)
            if channel is None:
                raise ValueError(
                    f"{date}: selected polarization {polarization} is missing from "
                    f"{case.band}{case.frequency}"
                )
            # The exact common window is attached after coverage validation.
            assets_by_identity[(date, polarization)] = (source, source_digest, channel, grid)

    assert common_grid is not None
    assert expected_identity is not None
    assert expected_version is not None
    crs = CRS.from_epsg(common_grid.epsg)
    projector = Transformer.from_crs(4326, crs, always_xy=True)
    projected_roi = transform(projector.transform, roi)
    projected_buffered = transform(projector.transform, buffered_roi)
    footprint = _grid_footprint(common_grid)
    coverage_tolerance = max(abs(common_grid.x_spacing), abs(common_grid.y_spacing)) * 1.0e-8
    for date, grid in inventories:
        scene = _grid_footprint(grid)
        roi_fraction = _coverage(scene, projected_roi)
        buffered_fraction = _coverage(scene, projected_buffered)
        coverages_roi.append((date, roi_fraction))
        coverages_buffered.append((date, buffered_fraction))
        if not scene.buffer(coverage_tolerance).covers(projected_buffered):
            raise ValueError(
                f"{date}: buffered AOI lies outside the GSLC scene; "
                f"coverage={buffered_fraction:.6f}"
            )

    west, south, east, north = projected_buffered.bounds
    column_start, column_stop = _axis_window(
        west, east, common_grid.x_start, common_grid.x_spacing, common_grid.shape[1]
    )
    row_start, row_stop = _axis_window(
        south, north, common_grid.y_start, common_grid.y_spacing, common_grid.shape[0]
    )
    window = GslcPixelWindow(
        row_start=row_start,
        row_stop=row_stop,
        column_start=column_start,
        column_stop=column_stop,
    )
    output = case.output_dir.resolve()
    existing_complete = False
    if output.exists():
        if not output.is_dir():
            raise FileExistsError(f"GSLC stack output conflicts with a file: {output}")
        required = ("registered_stack.json", "crop_window.json", "quality.json", "provenance.json")
        existing_complete = all((output / name).is_file() for name in required)
        if not existing_complete:
            raise FileExistsError(f"incomplete or conflicting GSLC stack output exists: {output}")
    dates = tuple(date for date, _ in dated_records)
    assets = tuple(
        NisarGslcAssetPlan(
            date=date,
            polarization=polarization,
            source_file=assets_by_identity[(date, polarization)][0],
            source_sha256=assets_by_identity[(date, polarization)][1],
            channel=assets_by_identity[(date, polarization)][2],
            grid=assets_by_identity[(date, polarization)][3],
            window=window,
        )
        for date in dates
        for polarization in case.polarizations
    )
    return NisarGslcStackPlan(
        case=case,
        product_type="GSLC",
        product_specification_version=expected_version,
        track=expected_identity[0],
        frame=expected_identity[1],
        orbit_direction=expected_identity[2],
        dates=dates,
        reference_date=case.reference_date,
        selected_polarizations=case.polarizations,
        grid=common_grid,
        window=window,
        roi=roi,
        buffered_roi=buffered_roi,
        roi_coverage=tuple(coverages_roi),
        buffered_coverage=tuple(coverages_buffered),
        assets=assets,
        existing_complete=existing_complete,
    )


__all__ = ["build_nisar_gslc_stack_plan"]
