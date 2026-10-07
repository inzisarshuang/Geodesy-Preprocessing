"""Tests for read-only NISAR RSLC HDF5 inventory inspection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import h5py
import numpy as np

from geodesy_preprocessing.sar.acquisition.nisar.hdf5 import inspect_nisar_rslc


def write_rslc(
    path: Path,
    *,
    bands: dict[str, dict[str, tuple[str, ...]]] | None = None,
    product_type: str = "RSLC",
) -> None:
    bands = bands or {"L": {"A": ("HH", "HV"), "B": ("HH",)}}
    with h5py.File(path, "w") as product:
        for band, frequencies in bands.items():
            root = product.create_group(f"science/{band}SAR")
            identification = root.create_group("identification")
            identification.create_dataset("productType", data=np.bytes_(product_type))
            identification.create_dataset("granuleId", data=np.bytes_(f"NISAR_{band}_TEST"))
            identification.create_dataset(
                "zeroDopplerStartTime", data=np.bytes_("2026-01-05T10:20:30Z")
            )
            identification.create_dataset(
                "zeroDopplerEndTime", data=np.bytes_("2026-01-05T10:21:02Z")
            )
            identification.create_dataset("trackNumber", data=np.int32(87))
            identification.create_dataset("frameNumber", data=np.int32(22))
            identification.create_dataset(
                "orbitPassDirection", data=np.bytes_("Descending")
            )
            swaths = root.create_group("RSLC/swaths")
            for frequency, polarizations in frequencies.items():
                frequency_group = swaths.create_group(f"frequency{frequency}")
                frequency_group.create_dataset("slantRange", data=np.arange(3))
                for polarization in polarizations:
                    frequency_group.create_dataset(
                        polarization,
                        shape=(2, 3),
                        dtype=np.complex64,
                    )


class NisarHdf5Tests(unittest.TestCase):
    """Expose only validated metadata and raster paths without reading arrays."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "product.h5"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_inspects_l_and_s_frequency_polarization_inventory(self) -> None:
        write_rslc(
            self.path,
            bands={"L": {"A": ("HH", "HV"), "B": ("HH",)}, "S": {"A": ("VV",)}},
        )

        inventory = inspect_nisar_rslc(self.path)

        self.assertEqual(inventory.path, self.path.resolve())
        self.assertEqual(inventory.product_type, "RSLC")
        self.assertEqual(inventory.acquisition_start, "2026-01-05T10:20:30Z")
        self.assertEqual(inventory.acquisition_end, "2026-01-05T10:21:02Z")
        self.assertEqual((inventory.track, inventory.frame), (87, 22))
        self.assertEqual(inventory.orbit_direction, "DESCENDING")
        self.assertEqual(inventory.bands, ("L", "S"))
        self.assertEqual(
            tuple((item.band, item.frequency, item.polarization) for item in inventory.channels),
            (("L", "A", "HH"), ("L", "A", "HV"), ("L", "B", "HH"), ("S", "A", "VV")),
        )
        channel = inventory.channels[0]
        self.assertEqual(channel.dataset_path, "/science/LSAR/RSLC/swaths/frequencyA/HH")
        self.assertEqual(channel.shape, (2, 3))
        self.assertEqual(channel.dtype, "complex64")

    def test_rejects_wrong_identity_missing_metadata_and_missing_raster(self) -> None:
        write_rslc(self.path, product_type="GSLC")
        with self.assertRaisesRegex(ValueError, "RSLC"):
            inspect_nisar_rslc(self.path)

        write_rslc(self.path)
        with h5py.File(self.path, "a") as product:
            del product["science/LSAR/identification/trackNumber"]
        with self.assertRaisesRegex(ValueError, "trackNumber"):
            inspect_nisar_rslc(self.path)

        write_rslc(self.path, bands={"L": {"A": ()}})
        with self.assertRaisesRegex(ValueError, "polarization|raster"):
            inspect_nisar_rslc(self.path)

    def test_rejects_corrupt_file_and_closes_handle_after_validation_error(self) -> None:
        self.path.write_bytes(b"not an HDF5 file")
        with self.assertRaisesRegex(ValueError, "HDF5"):
            inspect_nisar_rslc(self.path)

        write_rslc(self.path, product_type="GSLC")
        real_file = h5py.File
        opened = []

        def tracking_file(*args, **kwargs):
            handle = real_file(*args, **kwargs)
            opened.append(handle)
            return handle

        with mock.patch(
            "geodesy_preprocessing.sar.acquisition.nisar.hdf5.h5py.File",
            side_effect=tracking_file,
        ), self.assertRaises(ValueError):
            inspect_nisar_rslc(self.path)
        self.assertEqual(len(opened), 1)
        self.assertFalse(opened[0].id.valid)


if __name__ == "__main__":
    unittest.main()
