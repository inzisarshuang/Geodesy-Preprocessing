"""NISAR L2 GSLC HDF5 的只读身份、通道和地理网格清单。

功能概述:
    只读取产品身份和一维坐标等小型元数据，不读取复数影像数组；检查 GSLC 的产品
    类型、频率、极化、复数 dtype、EPSG、坐标方向、间距和形状。

函数说明:
    ``NisarGslcGrid`` 描述一个频率的规则地理网格；``NisarGslcChannel`` 描述一个
    复数数据集；``NisarGslcInventory`` 汇总一个文件；``inspect_nisar_gslc`` 执行
    只读检查并在成功或失败后关闭 HDF5 文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np


_POLARIZATIONS = ("HH", "HV", "VH", "VV")


@dataclass(frozen=True)
class NisarGslcGrid:
    """One regular, signed map-coordinate grid in a GSLC product."""

    band: str
    frequency: str
    epsg: int
    shape: tuple[int, int]
    x_start: float
    y_start: float
    x_spacing: float
    y_spacing: float
    center_frequency_hz: float
    x_coordinates_path: str
    y_coordinates_path: str


@dataclass(frozen=True)
class NisarGslcChannel:
    """One complex GSLC raster located on a validated frequency grid."""

    band: str
    frequency: str
    polarization: str
    dataset_path: str
    shape: tuple[int, int]
    dtype: str


@dataclass(frozen=True)
class NisarGslcInventory:
    """Validated identity, grids, and channel paths for one immutable GSLC file."""

    path: Path
    product_type: str
    granule_id: str
    acquisition_start: str
    acquisition_end: str
    track: int
    frame: int
    orbit_direction: str
    product_specification_version: str
    bands: tuple[str, ...]
    grids: tuple[NisarGslcGrid, ...]
    channels: tuple[NisarGslcChannel, ...]


def _scalar(group: h5py.Group, name: str) -> object:
    if name not in group or not isinstance(group[name], h5py.Dataset):
        raise ValueError(f"NISAR metadata is missing dataset {group.name}/{name}")
    value = group[name][()]
    if isinstance(value, np.ndarray):
        if value.size != 1:
            raise ValueError(f"NISAR metadata dataset {group.name}/{name} must be scalar")
        value = value.reshape(-1)[0]
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _text(group: h5py.Group, name: str) -> str:
    value = str(_scalar(group, name)).strip()
    if not value:
        raise ValueError(f"NISAR metadata dataset {group.name}/{name} must not be empty")
    return value


def _positive_integer(group: h5py.Group, name: str) -> int:
    value = _scalar(group, name)
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"NISAR metadata dataset {group.name}/{name} must be an integer") from error
    if result < 1:
        raise ValueError(f"NISAR metadata dataset {group.name}/{name} must be positive")
    return result


def _positive_float(group: h5py.Group, name: str) -> float:
    value = _scalar(group, name)
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"NISAR metadata dataset {group.name}/{name} must be numeric") from error
    if not np.isfinite(result) or result <= 0.0:
        raise ValueError(f"NISAR metadata dataset {group.name}/{name} must be positive and finite")
    return result


def _coordinate_axis(group: h5py.Group, name: str, spacing_name: str) -> tuple[np.ndarray, float]:
    if name not in group or not isinstance(group[name], h5py.Dataset):
        raise ValueError(f"NISAR GSLC grid is missing dataset {group.name}/{name}")
    dataset = group[name]
    if dataset.ndim != 1 or dataset.size < 2:
        raise ValueError(f"NISAR GSLC {name} must be a one-dimensional axis with at least two values")
    coordinates = np.asarray(dataset[...], dtype=np.float64)
    if not np.all(np.isfinite(coordinates)):
        raise ValueError(f"NISAR GSLC {name} must contain only finite values")
    differences = np.diff(coordinates)
    spacing = float(differences[0])
    tolerance = max(abs(spacing) * 1.0e-9, np.finfo(np.float64).eps * 16.0)
    if spacing == 0.0 or not np.all(np.signbit(differences) == np.signbit(spacing)):
        raise ValueError(f"NISAR GSLC {name} must be strictly monotonic")
    if not np.allclose(differences, spacing, rtol=0.0, atol=tolerance):
        raise ValueError(f"NISAR GSLC {name} must be regular")
    declared = float(_scalar(group, spacing_name))
    if not np.isfinite(declared) or not np.isclose(declared, spacing, rtol=0.0, atol=tolerance):
        raise ValueError(
            f"NISAR GSLC {spacing_name} disagrees with {name}: {declared} != {spacing}"
        )
    return coordinates, spacing


def inspect_nisar_gslc(path: str | Path) -> NisarGslcInventory:
    """Inspect one NISAR L2 GSLC without loading any complex raster array."""

    source = Path(path).expanduser().resolve(strict=True)
    try:
        product = h5py.File(source, "r")
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid NISAR HDF5 file: {source}") from error
    try:
        identity: tuple[str, str, str, int, int, str, str] | None = None
        granule_id = ""
        bands: list[str] = []
        grids: list[NisarGslcGrid] = []
        channels: list[NisarGslcChannel] = []
        for band in ("L", "S"):
            root_path = f"/science/{band}SAR"
            if root_path not in product:
                continue
            identification_path = f"{root_path}/identification"
            if identification_path not in product:
                raise ValueError(f"NISAR HDF5 is missing {identification_path}")
            identification = product[identification_path]
            product_type = _text(identification, "productType").upper()
            if product_type != "GSLC":
                raise ValueError(f"NISAR productType must be GSLC, got {product_type}")
            current = (
                product_type,
                _text(identification, "zeroDopplerStartTime"),
                _text(identification, "zeroDopplerEndTime"),
                _positive_integer(identification, "trackNumber"),
                _positive_integer(identification, "frameNumber"),
                _text(identification, "orbitPassDirection").upper(),
                _text(identification, "productSpecificationVersion"),
            )
            if current[5] not in {"ASCENDING", "DESCENDING"}:
                raise ValueError("NISAR orbitPassDirection must be ASCENDING or DESCENDING")
            if identity is None:
                identity = current
                granule_id = _text(identification, "granuleId")
            elif current != identity:
                raise ValueError("NISAR L/S identification metadata disagree")

            grids_path = f"{root_path}/GSLC/grids"
            if grids_path not in product:
                raise ValueError(f"NISAR HDF5 is missing {grids_path}")
            band_channel_count = 0
            for frequency in ("A", "B"):
                frequency_path = f"{grids_path}/frequency{frequency}"
                if frequency_path not in product:
                    continue
                group = product[frequency_path]
                x, x_spacing = _coordinate_axis(
                    group, "xCoordinates", "xCoordinateSpacing"
                )
                y, y_spacing = _coordinate_axis(
                    group, "yCoordinates", "yCoordinateSpacing"
                )
                epsg = _positive_integer(group, "projection")
                center_frequency = _positive_float(group, "centerFrequency")
                grid_shape = (int(y.size), int(x.size))
                grids.append(
                    NisarGslcGrid(
                        band=band,
                        frequency=frequency,
                        epsg=epsg,
                        shape=grid_shape,
                        x_start=float(x[0]),
                        y_start=float(y[0]),
                        x_spacing=x_spacing,
                        y_spacing=y_spacing,
                        center_frequency_hz=center_frequency,
                        x_coordinates_path=group["xCoordinates"].name,
                        y_coordinates_path=group["yCoordinates"].name,
                    )
                )
                for polarization in _POLARIZATIONS:
                    if polarization not in group:
                        continue
                    dataset = group[polarization]
                    if not isinstance(dataset, h5py.Dataset) or dataset.ndim != 2:
                        raise ValueError(
                            f"NISAR GSLC polarization raster {dataset.name} must be two-dimensional"
                        )
                    if not np.issubdtype(dataset.dtype, np.complexfloating):
                        raise ValueError(
                            f"NISAR GSLC polarization raster {dataset.name} must have a complex dtype"
                        )
                    shape = tuple(int(value) for value in dataset.shape)
                    if shape != grid_shape:
                        raise ValueError(
                            f"NISAR GSLC polarization raster {dataset.name} shape {shape} "
                            f"does not match grid shape {grid_shape}"
                        )
                    channels.append(
                        NisarGslcChannel(
                            band=band,
                            frequency=frequency,
                            polarization=polarization,
                            dataset_path=dataset.name,
                            shape=shape,
                            dtype=str(dataset.dtype),
                        )
                    )
                    band_channel_count += 1
            if band_channel_count == 0:
                raise ValueError(f"NISAR {band}-band GSLC has no complex polarization rasters")
            bands.append(band)
        if identity is None:
            raise ValueError("NISAR HDF5 contains neither /science/LSAR nor /science/SSAR")
        product_type, start, end, track, frame, direction, product_version = identity
        return NisarGslcInventory(
            path=source,
            product_type=product_type,
            granule_id=granule_id,
            acquisition_start=start,
            acquisition_end=end,
            track=track,
            frame=frame,
            orbit_direction=direction,
            product_specification_version=product_version,
            bands=tuple(bands),
            grids=tuple(grids),
            channels=tuple(channels),
        )
    finally:
        product.close()


__all__ = [
    "NisarGslcChannel",
    "NisarGslcGrid",
    "NisarGslcInventory",
    "inspect_nisar_gslc",
]
