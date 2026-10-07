"""NISAR 轨道覆盖率的等面积计算和稳定排序。

功能概述:
    把研究区与产品足迹在研究区中心的等面积坐标系中求交，正确处理跨 180° 经线
    的局部多边形，并按轨道汇总多景联合覆盖。

函数说明:
    ``NisarTrackCoverage`` 保存单轨道汇总；``coverage_fraction`` 计算单个足迹覆盖率；
    ``summarize_tracks`` 合并同轨产品并按覆盖率和轨道号排序。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from shapely import wkt
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geodesy_preprocessing.sar.common.coverage import (
    _fraction,
    _prepare_aoi,
    _project_footprint,
    coverage_fraction,
)

from .models import NisarGranule


@dataclass(frozen=True)
class NisarTrackCoverage:
    """One track's union coverage and stable product inventory."""

    track: int
    frames: tuple[int, ...]
    granule_count: int
    coverage_fraction: float
    granule_ids: tuple[str, ...]


def summarize_tracks(
    granules: Sequence[NisarGranule], aoi: BaseGeometry
) -> tuple[NisarTrackCoverage, ...]:
    """Union granule footprints per track and rank by coverage then track number."""

    reference, projected_aoi, transformer = _prepare_aoi(aoi)
    groups: dict[int, list[tuple[NisarGranule, BaseGeometry]]] = defaultdict(list)
    for granule in granules:
        try:
            footprint = wkt.loads(granule.footprint_wkt)
        except (GEOSException, TypeError) as error:
            raise ValueError(f"NISAR granule {granule.granule_id} has invalid footprint WKT") from error
        try:
            projected = _project_footprint(
                footprint,
                reference,
                transformer,
                label=f"NISAR granule {granule.granule_id} footprint",
            )
        except ValueError as error:
            raise ValueError(
                f"NISAR granule {granule.granule_id} has invalid footprint: {error}"
            ) from error
        groups[granule.track].append((granule, projected))
    summaries: list[NisarTrackCoverage] = []
    for track, entries in groups.items():
        ordered = sorted(entries, key=lambda item: (item[0].start_time, item[0].frame, item[0].granule_id))
        union = unary_union([projected for _, projected in ordered])
        summaries.append(
            NisarTrackCoverage(
                track=track,
                frames=tuple(sorted({granule.frame for granule, _ in ordered})),
                granule_count=len(ordered),
                coverage_fraction=_fraction(projected_aoi, union),
                granule_ids=tuple(granule.granule_id for granule, _ in ordered),
            )
        )
    return tuple(sorted(summaries, key=lambda item: (-item.coverage_fraction, item.track)))


__all__ = ["NisarTrackCoverage", "coverage_fraction", "summarize_tracks"]
