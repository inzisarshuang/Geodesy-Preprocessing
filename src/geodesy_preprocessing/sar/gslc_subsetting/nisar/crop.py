"""NISAR GSLC 复数窗口的分块读取、GeoTIFF 写出和振幅快视图。

功能概述:
    只读取计划指定的 HDF5 hyperslab，显式保持 complex64 复数值与相位；按原 GSLC
    网格写 GeoTIFF。PNG 是仅供查看的振幅派生图，不作为分析数据。

函数说明:
    ``iter_gslc_blocks`` 逐块返回输出行偏移和复数数组；
    ``write_gslc_complex_geotiff`` 写入不重投影、不重采样的复数 GeoTIFF；
    ``write_gslc_quicklook`` 生成确定性的有限值振幅 PNG。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import warnings

import h5py
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import Affine
from rasterio.windows import Window

from geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5 import NisarGslcChannel

from .models import GslcPixelWindow, NisarGslcAssetPlan


def _validate_window(window: GslcPixelWindow, shape: tuple[int, int]) -> None:
    if not isinstance(window, GslcPixelWindow):
        raise TypeError("window must be GslcPixelWindow")
    if (
        window.row_start < 0
        or window.column_start < 0
        or window.row_stop > shape[0]
        or window.column_stop > shape[1]
        or window.row_start >= window.row_stop
        or window.column_start >= window.column_stop
    ):
        raise ValueError(f"GSLC pixel window {window} lies outside channel shape {shape}")


def iter_gslc_blocks(
    source: str | Path,
    channel: NisarGslcChannel,
    window: GslcPixelWindow,
    block_rows: int = 512,
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield bounded complex64 hyperslabs as ``(output_row_offset, array)``."""

    if not isinstance(channel, NisarGslcChannel):
        raise TypeError("channel must be NisarGslcChannel")
    if not isinstance(block_rows, int) or isinstance(block_rows, bool) or block_rows < 1:
        raise ValueError("block_rows must be a positive integer")
    _validate_window(window, channel.shape)
    path = Path(source).expanduser().resolve(strict=True)
    try:
        product = h5py.File(path, "r")
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid NISAR GSLC HDF5 file: {path}") from error
    try:
        try:
            dataset = product[channel.dataset_path]
        except KeyError as error:
            raise ValueError(f"GSLC channel dataset is missing: {channel.dataset_path}") from error
        shape = tuple(int(value) for value in dataset.shape)
        if shape != channel.shape:
            raise ValueError(
                f"GSLC channel shape changed after planning: {shape} != {channel.shape}"
            )
        if not np.issubdtype(dataset.dtype, np.complexfloating):
            raise ValueError(f"GSLC channel is no longer complex: {channel.dataset_path}")
        for row_start in range(window.row_start, window.row_stop, block_rows):
            row_stop = min(row_start + block_rows, window.row_stop)
            values = dataset[
                row_start:row_stop,
                window.column_start : window.column_stop,
            ]
            block = np.asarray(values, dtype=np.complex64)
            expected = (row_stop - row_start, window.shape[1])
            if block.shape != expected:
                raise ValueError(
                    f"GSLC block shape changed while reading: {block.shape} != {expected}"
                )
            yield row_start - window.row_start, block
    finally:
        product.close()


def write_gslc_complex_geotiff(
    asset: NisarGslcAssetPlan,
    destination: str | Path,
    block_rows: int = 512,
) -> Path:
    """Write one planned GSLC window as complex64 without spatial interpolation."""

    if not isinstance(asset, NisarGslcAssetPlan):
        raise TypeError("asset must be NisarGslcAssetPlan")
    _validate_window(asset.window, asset.grid.shape)
    if asset.channel.shape != asset.grid.shape:
        raise ValueError("GSLC channel shape differs from its planned grid")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    height, width = asset.window.shape
    transform = Affine(
        asset.grid.x_spacing,
        0.0,
        asset.grid.x_start
        + (asset.window.column_start - 0.5) * asset.grid.x_spacing,
        0.0,
        asset.grid.y_spacing,
        asset.grid.y_start + (asset.window.row_start - 0.5) * asset.grid.y_spacing,
    )
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "complex64",
        "crs": f"EPSG:{asset.grid.epsg}",
        "transform": transform,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
        "zlevel": 6,
        "BIGTIFF": "IF_SAFER",
    }
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(target, "w", **profile) as output:
            for row_offset, block in iter_gslc_blocks(
                asset.source_file,
                asset.channel,
                asset.window,
                block_rows=block_rows,
            ):
                block_window = Window(0, row_offset, width, block.shape[0])
                output.write(block, 1, window=block_window)
                valid = np.isfinite(block.real) & np.isfinite(block.imag)
                output.write_mask(valid.astype(np.uint8) * 255, window=block_window)
    return target


def write_gslc_quicklook(complex_path: str | Path, destination: str | Path) -> Path:
    """Write a display-only 8-bit PNG from complex amplitude."""

    source = Path(complex_path).expanduser().resolve(strict=True)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source) as dataset:
        if dataset.count != 1 or dataset.dtypes[0] not in {"complex64", "complex128"}:
            raise ValueError(f"GSLC quicklook source must be one complex band: {source}")
        scale = min(1.0, 2048.0 / max(dataset.height, dataset.width))
        height = max(1, int(round(dataset.height * scale)))
        width = max(1, int(round(dataset.width * scale)))
        complex_values = dataset.read(
            1,
            out_shape=(height, width),
            masked=True,
            resampling=Resampling.nearest,
        )
    values = np.asarray(complex_values.data)
    amplitude = np.abs(values).astype(np.float64, copy=False)
    valid = (~np.ma.getmaskarray(complex_values)) & np.isfinite(amplitude)
    gray = np.zeros((height, width), dtype=np.uint8)
    if np.any(valid):
        display = np.log1p(amplitude[valid])
        low, high = np.percentile(display, (2.0, 98.0))
        if np.isfinite(low) and np.isfinite(high) and high > low:
            scaled = np.clip((np.log1p(amplitude) - low) / (high - low), 0.0, 1.0)
            gray[valid] = np.rint(scaled[valid] * 255.0).astype(np.uint8)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(
            target,
            "w",
            driver="PNG",
            height=height,
            width=width,
            count=1,
            dtype="uint8",
            zlevel=9,
        ) as output:
            output.write(gray, 1)
    return target


__all__ = [
    "iter_gslc_blocks",
    "write_gslc_complex_geotiff",
    "write_gslc_quicklook",
]
