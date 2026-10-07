"""GAMMA 普通雷达坐标 SLC 参数文件读取工具。

功能概述:
    严格读取 GAMMA 参数字段，并构建通过校验的 SLC 网格信息。

函数说明:
    ``read_gamma_value`` 读取唯一且非空的指定字段。
    ``read_slc_grid`` 读取并校验 SLC 的尺寸和像元间隔。
"""

from __future__ import annotations

import math
from pathlib import Path

from .models import GammaSlcGrid


def read_gamma_value(path: str | Path, key: str) -> str:
    """Return the value of exactly one named parameter line."""

    source = Path(path)
    label = str(key).removesuffix(":")
    values: list[str] = []
    for line in source.read_text(encoding="ascii").splitlines():
        head, separator, tail = line.partition(":")
        if separator and head.strip() == label:
            values.append(tail.strip())
    if len(values) != 1:
        raise ValueError(
            f"expected exactly one {label} field in {source}, found {len(values)}"
        )
    if not values[0]:
        raise ValueError(f"empty {label} field in {source}")
    return values[0]


def _positive_integer(path: Path, key: str) -> int:
    raw = read_gamma_value(path, key).split()[0]
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a positive integer in {path}") from error
    if value < 1:
        raise ValueError(f"{key} must be a positive integer in {path}")
    return value


def _positive_float(path: Path, key: str) -> float:
    raw = read_gamma_value(path, key).split()[0]
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a positive finite number in {path}") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be a positive finite number in {path}")
    return value


def read_slc_grid(path: str | Path) -> GammaSlcGrid:
    """Read and validate the dimensions and sampling of one GAMMA SLC."""

    source = Path(path)
    return GammaSlcGrid(
        range_samples=_positive_integer(source, "range_samples"),
        azimuth_lines=_positive_integer(source, "azimuth_lines"),
        range_spacing=_positive_float(source, "range_pixel_spacing"),
        azimuth_spacing=_positive_float(source, "azimuth_pixel_spacing"),
    )


__all__ = ["read_gamma_value", "read_slc_grid"]
