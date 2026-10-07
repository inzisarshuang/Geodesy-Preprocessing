"""GAMMA 普通雷达坐标 SLC 配准使用的数据模型。

功能概述:
    保存单景 SLC 资产及其经过校验的雷达坐标网格信息。

函数说明:
    ``GammaSlcAsset`` 描述一景带日期、极化和参数文件的 GAMMA SLC。
    ``GammaSlcGrid`` 描述 SLC 的行列数与距离向、方位向采样间隔。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_POLARIZATION = re.compile(r"[A-Z][A-Z0-9]*\Z")


@dataclass(frozen=True)
class GammaSlcAsset:
    """One dated and polarized GAMMA SLC with its parameter file."""

    date: str
    polarization: str
    data_path: Path
    parameter_path: Path

    def __post_init__(self) -> None:
        try:
            parsed = datetime.strptime(self.date, "%Y%m%d")
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid SLC date: {self.date!r}") from error
        if parsed.strftime("%Y%m%d") != self.date:
            raise ValueError(f"invalid SLC date: {self.date!r}")
        if not isinstance(self.polarization, str) or not _POLARIZATION.fullmatch(
            self.polarization
        ):
            raise ValueError(
                "SLC polarization must be a non-empty uppercase label"
            )
        for field in ("data_path", "parameter_path"):
            if not isinstance(getattr(self, field), Path):
                raise TypeError(f"SLC asset {field} must be a Path")
        if self.data_path == self.parameter_path:
            raise ValueError("SLC data and parameter paths must differ")


@dataclass(frozen=True)
class GammaSlcGrid:
    """Dimensions and pixel spacings read from one GAMMA SLC parameter file."""

    range_samples: int
    azimuth_lines: int
    range_spacing: float
    azimuth_spacing: float

    def __post_init__(self) -> None:
        for field in ("range_samples", "azimuth_lines"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        for field in ("range_spacing", "azimuth_spacing"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field} must be a positive finite number")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{field} must be a positive finite number")


__all__ = ["GammaSlcAsset", "GammaSlcGrid"]
