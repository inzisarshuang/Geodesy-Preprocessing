"""NISAR L1 RSLC HDF5 的只读结构清单。

功能概述:
    不读取复数影像数组，只校验产品身份、采集信息及 L/S 波段下 A/B 频率和极化
    数据集，并返回后续适配器可使用的绝对 HDF5 路径。

函数说明:
    ``NisarRslcChannel`` 描述一个复数影像数据集；``NisarRslcInventory`` 描述文件；
    ``inspect_nisar_rslc`` 只读检查文件并保证所有文件句柄及时关闭。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np


_POLARIZATIONS = ("HH", "HV", "VH", "VV")


@dataclass(frozen=True)
class NisarRslcChannel:
    """One complex RSLC raster located inside a NISAR HDF5 product."""

    band: str
    frequency: str
    polarization: str
    dataset_path: str
    shape: tuple[int, ...]
    dtype: str


@dataclass(frozen=True)
class NisarRslcInventory:
    """Validated metadata and channel paths for one immutable RSLC file."""

    path: Path
    product_type: str
    granule_id: str
    acquisition_start: str
    acquisition_end: str
    track: int
    frame: int
    orbit_direction: str
    bands: tuple[str, ...]
    channels: tuple[NisarRslcChannel, ...]


def _scalar(group: h5py.Group, name: str) -> object:
    if name not in group or not isinstance(group[name], h5py.Dataset):
        raise ValueError(f"NISAR identification is missing dataset {name}")
    value = group[name][()]
    if isinstance(value, np.ndarray):
        if value.size != 1:
            raise ValueError(f"NISAR identification dataset {name} must be scalar")
        value = value.reshape(-1)[0]
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _text(group: h5py.Group, name: str) -> str:
    value = str(_scalar(group, name)).strip()
    if not value:
        raise ValueError(f"NISAR identification dataset {name} must not be empty")
    return value


def _positive_integer(group: h5py.Group, name: str) -> int:
    value = _scalar(group, name)
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"NISAR identification dataset {name} must be an integer") from error
    if result < 1:
        raise ValueError(f"NISAR identification dataset {name} must be positive")
    return result


def inspect_nisar_rslc(path: str | Path) -> NisarRslcInventory:
    """Inspect one NISAR L1 RSLC HDF5 without reading any raster array."""

    source = Path(path).expanduser().resolve(strict=True)
    try:
        product = h5py.File(source, "r")
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid NISAR HDF5 file: {source}") from error
    try:
        metadata: tuple[str, str, str, int, int, str] | None = None
        granule_id = ""
        bands: list[str] = []
        channels: list[NisarRslcChannel] = []
        for band in ("L", "S"):
            root_path = f"/science/{band}SAR"
            if root_path not in product:
                continue
            root = product[root_path]
            identification_path = f"{root_path}/identification"
            if identification_path not in product:
                raise ValueError(f"NISAR HDF5 is missing {identification_path}")
            identification = product[identification_path]
            product_type = _text(identification, "productType").upper()
            if product_type != "RSLC":
                raise ValueError(f"NISAR productType must be RSLC, got {product_type}")
            current = (
                product_type,
                _text(identification, "zeroDopplerStartTime"),
                _text(identification, "zeroDopplerEndTime"),
                _positive_integer(identification, "trackNumber"),
                _positive_integer(identification, "frameNumber"),
                _text(identification, "orbitPassDirection").upper(),
            )
            if current[-1] not in {"ASCENDING", "DESCENDING"}:
                raise ValueError("NISAR orbitPassDirection must be ASCENDING or DESCENDING")
            if metadata is None:
                metadata = current
                granule_id = _text(identification, "granuleId")
            elif current != metadata:
                raise ValueError("NISAR L/S identification metadata disagree")
            swaths_path = f"{root_path}/RSLC/swaths"
            if swaths_path not in product:
                raise ValueError(f"NISAR HDF5 is missing {swaths_path}")
            swaths = product[swaths_path]
            band_channel_count = 0
            for frequency in ("A", "B"):
                frequency_name = f"frequency{frequency}"
                if frequency_name not in swaths:
                    continue
                frequency_group = swaths[frequency_name]
                for polarization in _POLARIZATIONS:
                    if polarization not in frequency_group:
                        continue
                    dataset = frequency_group[polarization]
                    if not isinstance(dataset, h5py.Dataset) or dataset.ndim < 2:
                        raise ValueError(
                            f"NISAR polarization raster {dataset.name} must be at least 2-D"
                        )
                    channels.append(
                        NisarRslcChannel(
                            band=band,
                            frequency=frequency,
                            polarization=polarization,
                            dataset_path=dataset.name,
                            shape=tuple(int(value) for value in dataset.shape),
                            dtype=str(dataset.dtype),
                        )
                    )
                    band_channel_count += 1
            if band_channel_count == 0:
                raise ValueError(f"NISAR {band}-band has no polarization raster datasets")
            bands.append(band)
        if metadata is None:
            raise ValueError("NISAR HDF5 contains neither /science/LSAR nor /science/SSAR")
        product_type, start, end, track, frame, direction = metadata
        return NisarRslcInventory(
            path=source,
            product_type=product_type,
            granule_id=granule_id,
            acquisition_start=start,
            acquisition_end=end,
            track=track,
            frame=frame,
            orbit_direction=direction,
            bands=tuple(bands),
            channels=tuple(channels),
        )
    finally:
        product.close()


__all__ = ["NisarRslcChannel", "NisarRslcInventory", "inspect_nisar_rslc"]
