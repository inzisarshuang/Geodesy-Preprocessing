"""Sentinel-1 下载目录布局解析。

功能概述:
    根据 acquisition case 的布局策略返回单个相对轨道的物理目录，同时保留历史
    ``Orbit_NNN`` 默认结构并支持明确单轨 case 的扁平结构。

函数说明:
    ``resolve_download_track_dir`` 返回下载与 SAFE 合并共同使用的轨道目录。
"""

from __future__ import annotations

from pathlib import Path

from .models import Sentinel1AcquisitionCase, Sentinel1DownloadLayout


def resolve_download_track_dir(
    case: Sentinel1AcquisitionCase,
    track: int,
) -> Path:
    """Return the physical directory for one relative orbit track."""

    if case.download_layout is Sentinel1DownloadLayout.FLAT:
        return case.download_dir
    return case.download_dir / f"Orbit_{track:03d}"


__all__ = ["resolve_download_track_dir"]
