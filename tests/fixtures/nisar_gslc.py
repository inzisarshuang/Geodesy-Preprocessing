"""Small, deterministic NISAR GSLC HDF5 fixtures for unit tests."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np


def write_gslc(
    path: Path,
    *,
    product_type: str = "GSLC",
    bands: dict[str, dict[str, tuple[str, ...]]] | None = None,
    x_coordinates: np.ndarray | None = None,
    y_coordinates: np.ndarray | None = None,
    raster_dtype=np.complex64,
    include_extra_groups: bool = False,
    granule_id: str | None = None,
    acquisition_start: str = "2026-01-05T10:20:30Z",
    acquisition_end: str = "2026-01-05T10:21:02Z",
    track: int = 87,
    frame: int = 22,
    orbit_direction: str = "Ascending",
    product_specification_version: str = "1.4.0",
    epsg: int = 32630,
) -> None:
    """Write a minimal product-shaped GSLC with tiny science arrays."""

    bands = bands or {"L": {"A": ("HH", "HV"), "B": ("HH",)}}
    x = np.asarray(
        [500010.0, 500030.0, 500050.0, 500070.0]
        if x_coordinates is None
        else x_coordinates,
        dtype=np.float64,
    )
    y = np.asarray(
        [4200090.0, 4200070.0, 4200050.0]
        if y_coordinates is None
        else y_coordinates,
        dtype=np.float64,
    )
    with h5py.File(path, "w") as product:
        for band, frequencies in bands.items():
            root = product.create_group(f"science/{band}SAR")
            identification = root.create_group("identification")
            identification.create_dataset("productType", data=np.bytes_(product_type))
            identification.create_dataset(
                "granuleId", data=np.bytes_(granule_id or f"NISAR_{band}_GSLC_TEST")
            )
            identification.create_dataset(
                "zeroDopplerStartTime", data=np.bytes_(acquisition_start)
            )
            identification.create_dataset(
                "zeroDopplerEndTime", data=np.bytes_(acquisition_end)
            )
            identification.create_dataset("trackNumber", data=np.int32(track))
            identification.create_dataset("frameNumber", data=np.int32(frame))
            identification.create_dataset(
                "orbitPassDirection", data=np.bytes_(orbit_direction)
            )
            identification.create_dataset(
                "productSpecificationVersion",
                data=np.bytes_(product_specification_version),
            )
            grids = root.create_group("GSLC/grids")
            for frequency, polarizations in frequencies.items():
                group = grids.create_group(f"frequency{frequency}")
                group.create_dataset("xCoordinates", data=x)
                group.create_dataset("yCoordinates", data=y)
                group.create_dataset("xCoordinateSpacing", data=np.float64(x[1] - x[0]))
                group.create_dataset("yCoordinateSpacing", data=np.float64(y[1] - y[0]))
                group.create_dataset("projection", data=np.int32(epsg))
                group.create_dataset(
                    "centerFrequency",
                    data=np.float64(1_257_476_312.0 if band == "L" else 3_200_000_000.0),
                )
                for polarization in polarizations:
                    values = np.arange(y.size * x.size, dtype=np.float32).reshape(y.size, x.size)
                    if np.issubdtype(np.dtype(raster_dtype), np.complexfloating):
                        values = (values + 1j * (values + 0.5)).astype(raster_dtype)
                    else:
                        values = values.astype(raster_dtype)
                    group.create_dataset(polarization, data=values)
                group.create_dataset("mask", data=np.zeros((y.size, x.size), dtype=np.uint8))
            if include_extra_groups:
                root.create_group("GSLC/metadata/futureExtension").create_dataset(
                    "unknownButValid", data=np.int16(1)
                )


__all__ = ["write_gslc"]
