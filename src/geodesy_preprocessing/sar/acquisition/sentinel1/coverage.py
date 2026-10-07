"""Optional backend-neutral Sentinel-1 burst coverage rendering.

功能概述:
    在明确请求覆盖图时延迟导入 Matplotlib，并原子保存 burst 与 ROI 轮廓。

函数说明:
    ``save_coverage`` 将可用 IW burst 和 Polygon/MultiPolygon ROI 绘制为 JPEG。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from .burst_table import read_iw_file


def save_coverage(
    iw_files: Mapping[int, str | Path],
    roi: BaseGeometry,
    path: str | Path,
) -> Path:
    """Render burst and ROI outlines and atomically publish one JPEG."""

    from matplotlib import pyplot as plt
    from matplotlib.patches import Polygon as MatplotlibPolygon

    destination = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    figure = None
    try:
        figure, axes = plt.subplots()
        for swath in sorted(iw_files):
            for row in read_iw_file(iw_files[swath]):
                latitudes = row[[1, 3, 5, 7]]
                longitudes = row[[2, 4, 6, 8]]
                axes.add_patch(
                    MatplotlibPolygon(
                        list(zip(longitudes.tolist(), latitudes.tolist())),
                        closed=True,
                        fill=False,
                        edgecolor="C0",
                    )
                )
        polygons = [roi] if roi.geom_type == "Polygon" else list(roi.geoms)
        for polygon in polygons:
            axes.add_patch(
                MatplotlibPolygon(
                    list(polygon.exterior.coords),
                    closed=True,
                    fill=False,
                    edgecolor="red",
                )
            )
        axes.autoscale()
        axes.set_aspect("equal", "box")
        figure.savefig(temporary, format="jpg", dpi=200)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if figure is not None:
            plt.close(figure)
    return destination


__all__ = ["save_coverage"]
