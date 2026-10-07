"""GAMMA ``SLC_coreg.py`` 最终质量结果的严格解析与分级。

功能概述:
    只接受完整结束且每项最终指标唯一的质量文件，将拟合标准差与剩余常数偏移
    分为完成、告警和失败三级。

函数说明:
    ``GammaCoregQuality`` 保存最终配准指标；``parse_coreg_quality`` 严格解析文件；
    ``classify_coreg_quality`` 按 case 阈值返回 ``complete``、``warning`` 或 ``failed``。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .coreg_config import GammaRadarQualityThresholds


_SOLUTION = re.compile(
    r"^final solution:\s+(\d+) offset estimates accepted out of (\d+) samples\s*$",
    re.MULTILINE,
)
_STDDEV = re.compile(
    r"^final model fit std\. dev\. \(samples\) range:\s+"
    r"([-+0-9.eE]+)\s+azimuth:\s+([-+0-9.eE]+)\s*$",
    re.MULTILINE,
)
_RANGE = re.compile(
    r"^final range offset poly\. coeff\.:\s+([-+0-9.eE]+)", re.MULTILINE
)
_AZIMUTH = re.compile(
    r"^final azimuth offset poly\. coeff\.:\s+([-+0-9.eE]+)", re.MULTILINE
)


@dataclass(frozen=True)
class GammaCoregQuality:
    """Final accepted count, fit deviations, and residual constant offsets."""

    accepted_offsets: int
    total_offsets: int
    range_stddev: float
    azimuth_stddev: float
    range_residual: float
    azimuth_residual: float


def _one(pattern: re.Pattern[str], text: str, label: str) -> tuple[str, ...]:
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ValueError(f"quality file must contain exactly one final {label}")
    value = matches[0]
    return value if isinstance(value, tuple) else (value,)


def _number(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"quality {label} must be numeric") from error
    if not math.isfinite(parsed):
        raise ValueError(f"quality {label} must be finite")
    return parsed


def parse_coreg_quality(path: str | Path) -> GammaCoregQuality:
    """Parse the unique final metrics from one completed quality file."""

    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if not any(
        marker in text
        for marker in ("end of SLC_coreg.py", "end of MLI_coreg_refine.py")
    ):
        raise ValueError(f"quality file is incomplete: {source}")
    accepted_raw, total_raw = _one(_SOLUTION, text, "solution")
    range_std_raw, azimuth_std_raw = _one(_STDDEV, text, "fit standard deviation")
    range_residual_raw = _one(_RANGE, text, "range residual")[0]
    azimuth_residual_raw = _one(_AZIMUTH, text, "azimuth residual")[0]
    accepted = int(accepted_raw)
    total = int(total_raw)
    if accepted < 1 or total < 1 or accepted > total:
        raise ValueError("quality accepted-offset counts are invalid")
    range_stddev = _number(range_std_raw, "range standard deviation")
    azimuth_stddev = _number(azimuth_std_raw, "azimuth standard deviation")
    if range_stddev < 0 or azimuth_stddev < 0:
        raise ValueError("quality standard deviations must not be negative")
    return GammaCoregQuality(
        accepted,
        total,
        range_stddev,
        azimuth_stddev,
        _number(range_residual_raw, "range residual"),
        _number(azimuth_residual_raw, "azimuth residual"),
    )


def classify_coreg_quality(
    quality: GammaCoregQuality, thresholds: GammaRadarQualityThresholds
) -> Literal["complete", "warning", "failed"]:
    """Classify final metrics against warning and hard-failure thresholds."""

    stddev = max(quality.range_stddev, quality.azimuth_stddev)
    residual = max(abs(quality.range_residual), abs(quality.azimuth_residual))
    if (
        stddev >= thresholds.failure_offset_stddev
        or residual >= thresholds.failure_residual_offset
    ):
        return "failed"
    if (
        stddev >= thresholds.warning_offset_stddev
        or residual >= thresholds.warning_residual_offset
    ):
        return "warning"
    return "complete"


__all__ = [
    "GammaCoregQuality",
    "classify_coreg_quality",
    "parse_coreg_quality",
]
