"""Sentinel-1 查询结果的统一覆盖审查图。

功能概述:
    用红色研究区、稳定逐轨颜色及经纬度网格绘制产品足迹，并原子发布 PNG。

函数说明:
    ``build_coverage_figure`` 构建图形；``save_coverage_figure`` 保存并关闭图形。
"""

from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from shapely import wkt
from shapely.geometry.base import BaseGeometry

from .models import Sentinel1Product


def _polygons(geometry: BaseGeometry):
    if geometry.geom_type == "Polygon":
        return (geometry,)
    if geometry.geom_type == "MultiPolygon":
        return tuple(geometry.geoms)
    raise ValueError("coverage geometry must be Polygon or MultiPolygon")


def build_coverage_figure(
    products: Sequence[Sentinel1Product], aoi: BaseGeometry, title: str
):
    """Build one deterministic Sentinel-1 footprint figure without saving it."""

    from matplotlib import pyplot as plt
    from matplotlib.patches import Polygon as PolygonPatch

    if aoi.is_empty or not aoi.is_valid:
        raise ValueError("AOI must be valid and non-empty")
    grouped: dict[int, list[Sentinel1Product]] = defaultdict(list)
    for product in products:
        grouped[product.relative_orbit].append(product)
    figure, axes = plt.subplots(figsize=(10, 8))
    colormap = plt.get_cmap("tab20")
    for track in sorted(grouped):
        entries = sorted(grouped[track], key=lambda item: (item.start_time, item.product_id))
        label = f"Track {track}: {len(entries)} product(s)"
        labelled = False
        for product in entries:
            for polygon in _polygons(wkt.loads(product.footprint_wkt)):
                axes.add_patch(PolygonPatch(
                    list(polygon.exterior.coords), closed=True, fill=False, linewidth=1.2,
                    edgecolor=colormap(track % colormap.N), label=label if not labelled else "_nolegend_",
                ))
                labelled = True
    for index, polygon in enumerate(_polygons(aoi)):
        axes.add_patch(PolygonPatch(
            list(polygon.exterior.coords), closed=True, fill=False, linewidth=2.2,
            edgecolor="red", label="AOI" if index == 0 else "_nolegend_", zorder=10,
        ))
    axes.set_title(f"{title}\n{len(products)} product(s), {len(grouped)} track(s)")
    axes.set_xlabel("Longitude (degrees)")
    axes.set_ylabel("Latitude (degrees)")
    axes.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    axes.autoscale()
    axes.set_aspect("equal", "box")
    handles, labels = axes.get_legend_handles_labels()
    if labels:
        order = sorted(range(len(labels)), key=lambda index: (labels[index] != "AOI", labels[index]))
        axes.legend([handles[index] for index in order], [labels[index] for index in order])
    figure.tight_layout()
    return figure


def save_coverage_figure(
    products: Sequence[Sentinel1Product], aoi: BaseGeometry, path: str | Path, title: str
) -> Path:
    """Build and atomically save one Sentinel-1 coverage PNG."""

    from matplotlib import pyplot as plt

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    figure = build_coverage_figure(products, aoi, title)
    try:
        figure.savefig(temporary, format="png", dpi=200)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
        plt.close(figure)
    return destination


__all__ = ["build_coverage_figure", "save_coverage_figure"]
