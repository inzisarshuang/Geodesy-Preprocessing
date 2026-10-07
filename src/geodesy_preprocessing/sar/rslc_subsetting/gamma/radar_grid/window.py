"""Aligned one-look crop windows derived from radar masks.

功能概述:
    把参考 MLI 掩膜边界向外换算到单视 RSLC，并安全限制在共同图像边界内。

函数说明:
    ``PixelBounds`` 表示半开 MLI 边界；``CropWindow`` 表示 GAMMA SLC_copy 窗口；
    ``align_crop_window`` 执行多视对齐；``window_from_radar_mask`` 从非零掩膜构建窗口。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PixelBounds:
    """Half-open column/row bounds on the reference MLI grid."""

    col_start: int
    row_start: int
    col_stop: int
    row_stop: int


@dataclass(frozen=True)
class CropWindow:
    """Zero-based one-look GAMMA crop offsets and dimensions."""

    roff: int
    nr: int
    loff: int
    nl: int
    mli_bounds: PixelBounds
    warnings: tuple[str, ...] = ()


def _positive(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def align_crop_window(
    bounds: PixelBounds,
    *,
    range_looks: int,
    azimuth_looks: int,
    rslc_width: int,
    rslc_nlines: int,
) -> CropWindow:
    """Scale half-open MLI bounds to an outward-aligned one-look RSLC window."""

    if not isinstance(bounds, PixelBounds):
        raise TypeError("bounds must be PixelBounds")
    rlks = _positive(range_looks, "range_looks")
    azlks = _positive(azimuth_looks, "azimuth_looks")
    width = _positive(rslc_width, "rslc_width")
    nlines = _positive(rslc_nlines, "rslc_nlines")
    represented_width = width // rlks * rlks
    represented_lines = nlines // azlks * azlks
    raw_left = bounds.col_start * rlks
    raw_top = bounds.row_start * azlks
    raw_right = bounds.col_stop * rlks
    raw_bottom = bounds.row_stop * azlks
    warnings: list[str] = []
    if raw_left < 0:
        warnings.append("left")
    if raw_top < 0:
        warnings.append("top")
    if raw_right > represented_width:
        warnings.append("right")
    if raw_bottom > represented_lines:
        warnings.append("bottom")
    left = max(0, min(raw_left, represented_width))
    top = max(0, min(raw_top, represented_lines))
    right = max(0, min(raw_right, represented_width))
    bottom = max(0, min(raw_bottom, represented_lines))
    if right <= left or bottom <= top:
        raise ValueError("crop window has zero size after clamping to the RSLC grid")
    return CropWindow(
        roff=left,
        nr=right - left,
        loff=top,
        nl=bottom - top,
        mli_bounds=bounds,
        warnings=tuple(warnings),
    )


def window_from_radar_mask(
    mask: np.ndarray,
    *,
    range_looks: int,
    azimuth_looks: int,
    rslc_width: int,
    rslc_nlines: int,
) -> CropWindow:
    """Calculate a common RSLC window from the non-zero extent of a 2-D MLI mask."""

    values = np.asarray(mask)
    if values.ndim != 2:
        raise ValueError("radar mask must be a two-dimensional array")
    rows, columns = np.nonzero(values)
    if rows.size == 0:
        raise ValueError("radar mask is empty")
    bounds = PixelBounds(
        int(columns.min()),
        int(rows.min()),
        int(columns.max()) + 1,
        int(rows.max()) + 1,
    )
    return align_crop_window(
        bounds,
        range_looks=range_looks,
        azimuth_looks=azimuth_looks,
        rslc_width=rslc_width,
        rslc_nlines=rslc_nlines,
    )


__all__ = ["CropWindow", "PixelBounds", "align_crop_window", "window_from_radar_mask"]
