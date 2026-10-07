"""Tests for read-only NISAR GSLC HDF5 inventory inspection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import h5py
import numpy as np

from geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5 import inspect_nisar_gslc
from tests.fixtures.nisar_gslc import write_gslc


class NisarGslcHdf5Tests(unittest.TestCase):
    """Require product identity, complex channels, and regular signed grids."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "product.h5"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_inspects_identity_channels_and_frequency_grids(self) -> None:
        write_gslc(
            self.path,
            bands={"L": {"A": ("HH", "HV"), "B": ("HH",)}, "S": {"A": ("VV",)}},
        )

        inventory = inspect_nisar_gslc(self.path)

        self.assertEqual(inventory.path, self.path.resolve())
        self.assertEqual(inventory.product_type, "GSLC")
        self.assertEqual(inventory.granule_id, "NISAR_L_GSLC_TEST")
        self.assertEqual(inventory.acquisition_start, "2026-01-05T10:20:30Z")
        self.assertEqual(inventory.acquisition_end, "2026-01-05T10:21:02Z")
        self.assertEqual((inventory.track, inventory.frame), (87, 22))
        self.assertEqual(inventory.orbit_direction, "ASCENDING")
        self.assertEqual(inventory.product_specification_version, "1.4.0")
        self.assertEqual(inventory.bands, ("L", "S"))
        self.assertEqual(
            tuple((item.band, item.frequency, item.polarization) for item in inventory.channels),
            (("L", "A", "HH"), ("L", "A", "HV"), ("L", "B", "HH"), ("S", "A", "VV")),
        )
        channel = inventory.channels[0]
        self.assertEqual(channel.dataset_path, "/science/LSAR/GSLC/grids/frequencyA/HH")
        self.assertEqual(channel.shape, (3, 4))
        self.assertEqual(channel.dtype, "complex64")
        grid = inventory.grids[0]
        self.assertEqual((grid.band, grid.frequency), ("L", "A"))
        self.assertEqual(grid.epsg, 32630)
        self.assertEqual(grid.shape, (3, 4))
        self.assertEqual((grid.x_start, grid.y_start), (500010.0, 4200090.0))
        self.assertEqual((grid.x_spacing, grid.y_spacing), (20.0, -20.0))
        self.assertEqual(grid.center_frequency_hz, 1_257_476_312.0)

    def test_gslc_inspector_allows_extra_groups_but_requires_core_schema(self) -> None:
        write_gslc(self.path, include_extra_groups=True)
        self.assertEqual(len(inspect_nisar_gslc(self.path).channels), 3)

        with h5py.File(self.path, "a") as product:
            del product["science/LSAR/GSLC/grids/frequencyA/projection"]
        with self.assertRaisesRegex(ValueError, "projection"):
            inspect_nisar_gslc(self.path)

        write_gslc(self.path, product_type="RSLC")
        with self.assertRaisesRegex(ValueError, "GSLC"):
            inspect_nisar_gslc(self.path)

    def test_gslc_grid_preserves_axis_direction_and_rejects_irregular_coordinates(self) -> None:
        write_gslc(self.path)
        grid = inspect_nisar_gslc(self.path).grids[0]
        self.assertGreater(grid.x_spacing, 0.0)
        self.assertLess(grid.y_spacing, 0.0)

        write_gslc(
            self.path,
            x_coordinates=np.array([500010.0, 500030.0, 500051.0, 500070.0]),
        )
        with self.assertRaisesRegex(ValueError, "xCoordinates.*regular"):
            inspect_nisar_gslc(self.path)

        write_gslc(
            self.path,
            y_coordinates=np.array([4200050.0, 4200070.0, 4200090.0]),
        )
        grid = inspect_nisar_gslc(self.path).grids[0]
        self.assertGreater(grid.y_spacing, 0.0)

    def test_rejects_real_science_raster_and_grid_shape_mismatch(self) -> None:
        write_gslc(self.path, raster_dtype=np.float32)
        with self.assertRaisesRegex(ValueError, "complex"):
            inspect_nisar_gslc(self.path)

        write_gslc(self.path)
        with h5py.File(self.path, "a") as product:
            group = product["science/LSAR/GSLC/grids/frequencyA"]
            del group["HH"]
            group.create_dataset("HH", shape=(2, 4), dtype=np.complex64)
        with self.assertRaisesRegex(ValueError, "shape"):
            inspect_nisar_gslc(self.path)

    def test_validation_error_closes_hdf5_handle(self) -> None:
        write_gslc(self.path, product_type="RSLC")
        real_file = h5py.File
        opened = []

        def tracking_file(*args, **kwargs):
            handle = real_file(*args, **kwargs)
            opened.append(handle)
            return handle

        with mock.patch(
            "geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5.h5py.File",
            side_effect=tracking_file,
        ), self.assertRaises(ValueError):
            inspect_nisar_gslc(self.path)
        self.assertEqual(len(opened), 1)
        self.assertFalse(opened[0].id.valid)


if __name__ == "__main__":
    unittest.main()
