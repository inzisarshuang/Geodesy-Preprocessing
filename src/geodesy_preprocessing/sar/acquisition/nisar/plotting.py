"""NISAR 查询结果的 S1 风格覆盖审查图。

功能概述:
    用红色研究区、稳定的逐轨颜色、经纬度网格和产品/轨道/帧计数绘制 NISAR
    足迹，并以 PNG 原子发布。

函数说明:
    ``build_coverage_figure`` 构建可检查的 Matplotlib 图；``save_coverage_figure``
    保存并关闭图形，返回最终 PNG 路径。
"""

from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from shapely import wkt
from shapely.geometry.base import BaseGeometry

from .models import NisarGranule


def _polygons(geometry: BaseGeometry):
    if geometry.geom_type == "Polygon":
        return (geometry,)
    if geometry.geom_type == "MultiPolygon":
        return tuple(geometry.geoms)
    raise ValueError("coverage geometry must be Polygon or MultiPolygon")


def build_coverage_figure(
    granules: Sequence[NisarGranule],
    aoi: BaseGeometry,
    title: str,
):
    """Build one deterministic NISAR footprint figure without saving it."""

    from matplotlib import pyplot as plt
    from matplotlib.patches import Polygon as PolygonPatch

    if aoi.is_empty or not aoi.is_valid:
        raise ValueError("AOI must be valid and non-empty")
    aoi_polygons = _polygons(aoi)
    grouped: dict[int, list[NisarGranule]] = defaultdict(list)
    for granule in granules:
        grouped[granule.track].append(granule)
    figure, axes = plt.subplots(figsize=(10, 8))
    colormap = plt.get_cmap("tab20")
    for track in sorted(grouped):
        entries = sorted(
            grouped[track], key=lambda item: (item.start_time, item.frame, item.granule_id)
        )
        frames = sorted({item.frame for item in entries})
        product_word = "product" if len(entries) == 1 else "products"
        frame_word = "frame" if len(frames) == 1 else "frames"
        label = (
            f"Track {track}: {len(entries)} {product_word}, "
            f"{frame_word} {','.join(str(frame) for frame in frames)}"
        )
        labelled = False
        for granule in entries:
            footprint = wkt.loads(granule.footprint_wkt)
            for polygon in _polygons(footprint):
                axes.add_patch(
                    PolygonPatch(
                        list(polygon.exterior.coords),
                        closed=True,
                        fill=False,
                        linewidth=1.2,
                        edgecolor=colormap(track % colormap.N),
                        label=label if not labelled else "_nolegend_",
                    )
                )
                labelled = True
    for index, polygon in enumerate(aoi_polygons):
        axes.add_patch(
            PolygonPatch(
                list(polygon.exterior.coords),
                closed=True,
                fill=False,
                linewidth=2.2,
                edgecolor="red",
                label="AOI" if index == 0 else "_nolegend_",
                zorder=10,
            )
        )
    track_word = "track" if len(grouped) == 1 else "tracks"
    product_word = "product" if len(granules) == 1 else "products"
    axes.set_title(f"{title}\n{len(granules)} {product_word}, {len(grouped)} {track_word}")
    axes.set_xlabel("Longitude (degrees)")
    axes.set_ylabel("Latitude (degrees)")
    axes.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    axes.autoscale()
    axes.set_aspect("equal", "box")
    handles, labels = axes.get_legend_handles_labels()
    if labels:
        order = sorted(
            range(len(labels)),
            key=lambda index: (labels[index] != "AOI", labels[index]),
        )
        axes.legend([handles[index] for index in order], [labels[index] for index in order])
    figure.tight_layout()
    return figure


def save_coverage_figure(
    granules: Sequence[NisarGranule],
    aoi: BaseGeometry,
    path: str | Path,
    title: str,
) -> Path:
    """Build and atomically save one NISAR coverage PNG."""

    from matplotlib import pyplot as plt

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    figure = build_coverage_figure(granules, aoi, title)
    try:
        figure.savefig(temporary, format="png", dpi=200)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
        plt.close(figure)
    return destination


__all__ = ["build_coverage_figure", "save_coverage_figure"]
