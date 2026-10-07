"""KML rasterization, coverage measurement and GAMMA radar-mask generation.

功能概述:
    在参考地图网格统计 KML 覆盖率，经 lt_fine 生成雷达掩膜，并发布可检查的几何产品。

函数说明:
    ``CoverageReport`` 记录原始和缓冲覆盖；``CropGeometryResult`` 汇总窗口与文件；
    ``validate_coverage`` 执行阈值保护；``build_crop_geometry`` 生成全部几何产品。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import mapping
from shapely.ops import transform

from .gamma import GammaCommandRunner
from .plan import GammaRadarCropPlan
from .window import CropWindow, window_from_radar_mask


@dataclass(frozen=True)
class CoverageReport:
    """Map-grid coverage counts and ratios for raw and buffered AOIs."""

    original_pixels: int
    original_valid_pixels: int
    original_ratio: float
    buffered_pixels: int
    buffered_valid_pixels: int
    buffered_ratio: float


@dataclass(frozen=True)
class CropGeometryResult:
    """Validated crop window and its human-inspectable artifacts."""

    coverage: CoverageReport
    window: CropWindow
    buffered_geojson: Path
    map_mask_tif: Path
    radar_mask_bmp: Path


def validate_coverage(
    original_mask: np.ndarray,
    buffered_mask: np.ndarray,
    valid_lookup_mask: np.ndarray,
    require_full_aoi_coverage: bool,
    min_buffer_coverage: float,
) -> CoverageReport:
    """Measure coverage on one map grid and enforce configured thresholds."""

    original = np.asarray(original_mask, dtype=bool)
    buffered = np.asarray(buffered_mask, dtype=bool)
    valid = np.asarray(valid_lookup_mask, dtype=bool)
    if original.shape != buffered.shape or original.shape != valid.shape:
        raise ValueError("AOI and lookup masks must use the same map grid")
    original_pixels = int(original.sum())
    buffered_pixels = int(buffered.sum())
    if original_pixels == 0 or buffered_pixels == 0:
        raise ValueError("AOI map mask is empty")
    original_valid = int(np.count_nonzero(original & valid))
    buffered_valid = int(np.count_nonzero(buffered & valid))
    original_ratio = original_valid / original_pixels
    buffered_ratio = buffered_valid / buffered_pixels
    if require_full_aoi_coverage and original_valid != original_pixels:
        raise ValueError(
            f"original AOI coverage is {original_ratio:.3%}; 100% is required"
        )
    if buffered_ratio < min_buffer_coverage:
        raise ValueError(
            f"buffered AOI coverage is {buffered_ratio:.3%}; "
            f"minimum is {min_buffer_coverage:.3%}"
        )
    return CoverageReport(
        original_pixels,
        original_valid,
        original_ratio,
        buffered_pixels,
        buffered_valid,
        buffered_ratio,
    )


def _map_mask(plan: GammaRadarCropPlan, geometry) -> np.ndarray:
    transformer = Transformer.from_crs(4326, plan.map_grid.crs, always_xy=True)
    projected = transform(transformer.transform, geometry)
    return rasterize(
        ((mapping(projected), 1),),
        out_shape=(plan.map_grid.nlines, plan.map_grid.width),
        transform=plan.map_grid.transform,
        fill=0,
        dtype="uint8",
    )


def _read_uchar(path: Path, width: int, nlines: int, label: str) -> np.ndarray:
    expected = width * nlines
    if not path.is_file() or path.stat().st_size != expected:
        actual = path.stat().st_size if path.exists() else 0
        raise ValueError(f"{label} size is {actual} bytes; expected {expected}")
    return np.fromfile(path, dtype=np.uint8).reshape((nlines, width))


def _write_tif(path: Path, values: np.ndarray, plan: GammaRadarCropPlan) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=plan.map_grid.width,
        height=plan.map_grid.nlines,
        count=1,
        dtype="uint8",
        crs=plan.map_grid.crs,
        transform=plan.map_grid.transform,
        compress="lzw",
    ) as dataset:
        dataset.write(values, 1)


def _write_bmp(path: Path, values: np.ndarray) -> None:
    with rasterio.Env(GDAL_PAM_ENABLED="NO"):
        with rasterio.open(
            path,
            "w",
            driver="BMP",
            width=values.shape[1],
            height=values.shape[0],
            count=1,
            dtype="uint8",
            transform=from_origin(0.0, float(values.shape[0]), 1.0, 1.0),
        ) as dataset:
            dataset.write(np.where(values, 255, 0).astype(np.uint8), 1)


def build_crop_geometry(
    plan: GammaRadarCropPlan,
    work_dir: Path,
    *,
    runner=None,
) -> CropGeometryResult:
    """Generate coverage masks, a radar mask and one aligned crop window."""

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=False)
    original = _map_mask(plan, plan.roi)
    buffered = _map_mask(plan, plan.buffered_roi)
    original_raw = work / "aoi_mask_original.raw"
    buffered_raw = work / "aoi_mask_map.raw"
    radar_ones = work / "radar_valid_ones.raw"
    valid_map_raw = work / "valid_lookup_map.raw"
    radar_raw = work / "aoi_mask_radar.raw"
    original_raw.write_bytes(original.tobytes())
    buffered_raw.write_bytes(buffered.tobytes())
    radar_ones.write_bytes(
        np.ones((plan.mli_grid.nlines, plan.mli_grid.width), dtype=np.uint8).tobytes()
    )
    command_runner = runner or GammaCommandRunner()
    command_runner.run(
        (
            "geocode_back",
            str(radar_ones),
            str(plan.mli_grid.width),
            str(plan.lookup_table),
            str(valid_map_raw),
            str(plan.map_grid.width),
            str(plan.map_grid.nlines),
            "0",
            "3",
        )
    )
    valid_map = _read_uchar(
        valid_map_raw, plan.map_grid.width, plan.map_grid.nlines, "valid lookup map"
    )
    coverage = validate_coverage(
        original,
        buffered,
        valid_map,
        plan.config.require_full_aoi_coverage,
        plan.config.min_buffer_coverage,
    )
    command_runner.run(
        (
            "geocode",
            str(plan.lookup_table),
            str(buffered_raw),
            str(plan.map_grid.width),
            str(radar_raw),
            str(plan.mli_grid.width),
            str(plan.mli_grid.nlines),
            "1",
            "3",
        )
    )
    radar = _read_uchar(
        radar_raw, plan.mli_grid.width, plan.mli_grid.nlines, "radar AOI mask"
    )
    window = window_from_radar_mask(
        radar,
        range_looks=plan.mli_grid.range_looks,
        azimuth_looks=plan.mli_grid.azimuth_looks,
        rslc_width=plan.rslc_grid.width,
        rslc_nlines=plan.rslc_grid.nlines,
    )
    geojson = work / "buffered_aoi.geojson"
    geojson.write_text(
        json.dumps(
            {"type": "Feature", "properties": {}, "geometry": mapping(plan.buffered_roi)},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    map_tif = work / "aoi_mask_map.tif"
    radar_bmp = work / "aoi_mask_radar.bmp"
    _write_tif(map_tif, buffered, plan)
    _write_bmp(radar_bmp, radar)
    return CropGeometryResult(coverage, window, geojson, map_tif, radar_bmp)


__all__ = [
    "CoverageReport",
    "CropGeometryResult",
    "build_crop_geometry",
    "validate_coverage",
]
