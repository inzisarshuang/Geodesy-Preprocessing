"""Sentinel-1 方向、轨道、覆盖率和 BURST 补齐选择。

功能概述:
    在本地按极化包含关系、方向和轨道筛选产品，以等面积联合覆盖率选择能让每期
    达标的最少轨道，并明确区分研究区命中 BURST 与合并所需补充 BURST。

函数说明:
    ``Sentinel1Selection`` 保存选择结果与逐日期覆盖率；
    ``select_sentinel1_products`` 执行确定性的本地选择。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from itertools import combinations
from collections.abc import Sequence

from shapely import wkt
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geodesy_preprocessing.sar.common.coverage import (
    _fraction,
    _prepare_aoi,
    _project_footprint,
)

from .models import Sentinel1AcquisitionCase, Sentinel1Product, Sentinel1ProductType


@dataclass(frozen=True)
class Sentinel1Selection:
    """One deterministic product selection and its coverage evidence."""

    orbit_direction: str
    tracks: tuple[int, ...]
    products: tuple[Sentinel1Product, ...]
    date_coverage: tuple[tuple[str, float], ...]


def _sort_key(product: Sentinel1Product) -> tuple[object, ...]:
    return (
        product.acquisition_date,
        product.relative_orbit,
        product.swath or "",
        product.relative_burst_id or 0,
        product.product_id,
    )


def _validate_burst_identity(products: Sequence[Sentinel1Product]) -> None:
    for product in products:
        if product.product_type is not Sentinel1ProductType.BURST:
            continue
        if (
            not product.swath
            or product.burst_index is None
            or product.relative_burst_id is None
            or not product.full_burst_id
            or not product.merge_group
        ):
            raise ValueError(
                f"Sentinel-1 product {product.product_id} has incomplete burst identity"
            )


def _projected_products(
    products: Sequence[Sentinel1Product], aoi: BaseGeometry
) -> tuple[BaseGeometry, dict[str, BaseGeometry]]:
    reference, projected_aoi, transformer = _prepare_aoi(aoi)
    projected: dict[str, BaseGeometry] = {}
    for product in products:
        try:
            geometry = wkt.loads(product.footprint_wkt)
            projected[product.product_id] = _project_footprint(
                geometry,
                reference,
                transformer,
                label=f"Sentinel-1 product {product.product_id} footprint",
            )
        except (GEOSException, TypeError, ValueError) as error:
            raise ValueError(
                f"Sentinel-1 product {product.product_id} has invalid footprint"
            ) from error
    return projected_aoi, projected


def _coverage(
    products: Sequence[Sentinel1Product],
    projected_aoi: BaseGeometry,
    projected: dict[str, BaseGeometry],
) -> tuple[tuple[str, float], ...]:
    by_date: dict[str, list[BaseGeometry]] = defaultdict(list)
    for product in products:
        by_date[product.acquisition_date].append(projected[product.product_id])
    return tuple(
        (date, _fraction(projected_aoi, unary_union(by_date[date])))
        for date in sorted(by_date)
    )


def _track_choice(
    products: Sequence[Sentinel1Product],
    dates: tuple[str, ...],
    threshold: float,
    allowed_tracks: tuple[int, ...] | None,
    projected_aoi: BaseGeometry,
    projected: dict[str, BaseGeometry],
) -> tuple[tuple[int, ...], tuple[tuple[str, float], ...], tuple[Sentinel1Product, ...]] | None:
    available = tuple(sorted({item.relative_orbit for item in products}))
    candidates = []
    lengths = (len(allowed_tracks),) if allowed_tracks is not None else range(1, len(available) + 1)
    for length in lengths:
        tracks_to_try = (allowed_tracks,) if allowed_tracks is not None else combinations(available, length)
        for tracks in tracks_to_try:
            selected = tuple(item for item in products if item.relative_orbit in tracks)
            coverage = _coverage(selected, projected_aoi, projected)
            values = dict(coverage)
            if dates and all(values.get(date, 0.0) + 1e-12 >= threshold for date in dates):
                candidates.append((tracks, coverage, selected))
        if candidates:
            break
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            -sum(value for _, value in item[1]),
            len(item[2]),
            item[0],
        ),
    )


def select_sentinel1_products(
    case: Sentinel1AcquisitionCase,
    products: Sequence[Sentinel1Product],
    aoi: BaseGeometry,
) -> Sentinel1Selection:
    """Select one direction and the smallest per-date covering track set."""

    typed = tuple(item for item in products if item.product_type is case.product_type)
    _validate_burst_identity(typed)
    projected_aoi, projected = _projected_products(typed, aoi)
    requested_polarizations = set(case.polarizations or ())
    locally_valid = tuple(
        item
        for item in typed
        if item.beam_mode == case.beam_mode
        and requested_polarizations.issubset(item.polarizations)
        and projected[item.product_id].intersects(projected_aoi)
    )
    if not locally_valid:
        raise ValueError("no Sentinel-1 products match beam, polarization, and AOI")
    dates = tuple(sorted({item.acquisition_date for item in locally_valid}))
    directions = (
        (case.orbit_direction,)
        if case.orbit_direction is not None
        else tuple(sorted({item.orbit_direction for item in locally_valid}))
    )
    choices = []
    for direction in directions:
        directed = tuple(item for item in locally_valid if item.orbit_direction == direction)
        choice = _track_choice(
            directed,
            dates,
            case.coverage_threshold,
            case.tracks,
            projected_aoi,
            projected,
        )
        if choice is not None:
            tracks, coverage, selected = choice
            choices.append((direction, tracks, coverage, selected))
    if not choices:
        raise ValueError("no Sentinel-1 direction/track choice meets the per-date coverage threshold")
    direction, tracks, date_coverage, selected = min(
        choices,
        key=lambda item: (
            len(item[1]),
            len(item[3]),
            -sum(value for _, value in item[2]),
            item[0],
            item[1],
        ),
    )
    selected_ids = {item.product_id for item in selected}
    marked = [replace(item, roi_match=True, supplemental=False) for item in selected]
    if case.product_type is Sentinel1ProductType.BURST:
        groups = {
            (item.acquisition_date, item.relative_orbit, item.merge_group)
            for item in selected
        }
        for item in typed:
            key = (item.acquisition_date, item.relative_orbit, item.merge_group)
            if (
                item.product_id not in selected_ids
                and key in groups
                and item.orbit_direction == direction
                and requested_polarizations.issubset(item.polarizations)
            ):
                marked.append(replace(item, roi_match=False, supplemental=True))
    return Sentinel1Selection(
        orbit_direction=direction,
        tracks=tuple(tracks),
        products=tuple(sorted(marked, key=_sort_key)),
        date_coverage=date_coverage,
    )


__all__ = ["Sentinel1Selection", "select_sentinel1_products"]
