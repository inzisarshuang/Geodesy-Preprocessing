"""Tests for phase-preserving, bounded NISAR GSLC extraction."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

import numpy as np
import rasterio
import h5py
from rasterio.errors import NotGeoreferencedWarning

from geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5 import (
    NisarGslcChannel,
    inspect_nisar_gslc,
)
from geodesy_preprocessing.sar.gslc_subsetting.nisar.crop import (
    iter_gslc_blocks,
    write_gslc_complex_geotiff,
    write_gslc_quicklook,
)
from geodesy_preprocessing.sar.gslc_subsetting.nisar.models import (
    GslcPixelWindow,
    NisarGslcAssetPlan,
)
from tests.fixtures.nisar_gslc import write_gslc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _TrackingDataset:
    def __init__(self, values: np.ndarray, name: str) -> None:
        self.values = values
        self.name = name
        self.shape = values.shape
        self.dtype = values.dtype
        self.ndim = values.ndim
        self.calls = []

    def __getitem__(self, key):
        self.calls.append(key)
        return self.values[key]


class _FakeProduct:
    def __init__(self, path: str, dataset: _TrackingDataset) -> None:
        self.path = path
        self.dataset = dataset

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self) -> None:
        return None

    def __getitem__(self, key):
        if key != self.path:
            raise KeyError(key)
        return self.dataset


class NisarGslcCropTests(unittest.TestCase):
    """Keep reads bounded and science pixels unchanged in the output GeoTIFF."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.h5"
        write_gslc(self.source, bands={"L": {"A": ("HH",)}})
        inventory = inspect_nisar_gslc(self.source)
        self.window = GslcPixelWindow(0, 3, 1, 4)
        self.asset = NisarGslcAssetPlan(
            date="20260105",
            polarization="HH",
            source_file=self.source,
            source_sha256=_sha256(self.source),
            channel=inventory.channels[0],
            grid=inventory.grids[0],
            window=self.window,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_iter_gslc_blocks_reads_only_requested_hyperslabs(self) -> None:
        values = (
            np.arange(7 * 8, dtype=np.float32).reshape(7, 8)
            + 1j * np.arange(7 * 8, dtype=np.float32).reshape(7, 8)
        ).astype(np.complex64)
        channel = NisarGslcChannel("L", "A", "HH", "/science/HH", (7, 8), "complex64")
        window = GslcPixelWindow(1, 7, 2, 6)
        tracked = _TrackingDataset(values, channel.dataset_path)

        with mock.patch(
            "geodesy_preprocessing.sar.gslc_subsetting.nisar.crop.h5py.File",
            return_value=_FakeProduct(channel.dataset_path, tracked),
        ):
            blocks = list(iter_gslc_blocks(self.source, channel, window, block_rows=2))

        self.assertEqual([offset for offset, _ in blocks], [0, 2, 4])
        self.assertTrue(all(block.shape == (2, 4) for _, block in blocks))
        self.assertTrue(all(block.dtype == np.complex64 for _, block in blocks))
        self.assertEqual(
            tracked.calls,
            [
                (slice(1, 3), slice(2, 6)),
                (slice(3, 5), slice(2, 6)),
                (slice(5, 7), slice(2, 6)),
            ],
        )
        np.testing.assert_array_equal(np.vstack([block for _, block in blocks]), values[1:7, 2:6])

    def test_complex_geotiff_preserves_values_phase_grid_and_source(self) -> None:
        source_before = (self.source.stat(), _sha256(self.source))
        destination = self.root / "output" / "crop.tif"

        result = write_gslc_complex_geotiff(self.asset, destination, block_rows=2)

        self.assertEqual(result, destination)
        with rasterio.open(destination) as dataset:
            output = dataset.read(1)
            mask = dataset.read_masks(1)
            self.assertEqual(dataset.dtypes, ("complex64",))
            self.assertEqual((dataset.height, dataset.width), (3, 3))
            self.assertEqual(dataset.crs.to_epsg(), 32630)
            self.assertEqual(tuple(dataset.transform)[:6], (20.0, 0.0, 500020.0, 0.0, -20.0, 4200100.0))
            self.assertIsNone(dataset.nodata)
            self.assertTrue(np.all(mask == 255))
            self.assertTrue(dataset.profile["tiled"])
            self.assertEqual(dataset.profile["compress"], "deflate")
        with h5py.File(self.source, "r") as product:
            expected = np.asarray(product[self.asset.channel.dataset_path][:, 1:4], dtype=np.complex64)
        np.testing.assert_array_equal(output, expected)
        np.testing.assert_allclose(np.angle(output), np.angle(expected), rtol=0.0, atol=0.0)
        self.assertEqual((self.source.stat(), _sha256(self.source)), source_before)

    def test_complex_geotiff_masks_nonfinite_complex_pixels_without_nodata_sentinel(self) -> None:
        with h5py.File(self.source, "a") as product:
            product[self.asset.channel.dataset_path][1, 2] = np.complex64(np.nan + 1j * np.nan)
        destination = write_gslc_complex_geotiff(self.asset, self.root / "masked.tif")

        with rasterio.open(destination) as dataset:
            self.assertIsNone(dataset.nodata)
            mask = dataset.read_masks(1)
        expected_mask = np.full(mask.shape, 255, dtype=np.uint8)
        expected_mask[1, 1] = 0
        np.testing.assert_array_equal(mask, expected_mask)

    def test_quicklook_is_deterministic_amplitude_png_and_does_not_mutate_tiff(self) -> None:
        complex_path = write_gslc_complex_geotiff(
            self.asset, self.root / "crop.tif", block_rows=2
        )
        before = (complex_path.stat(), _sha256(complex_path))
        first = self.root / "first.png"
        second = self.root / "second.png"

        write_gslc_quicklook(complex_path, first)
        write_gslc_quicklook(complex_path, second)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(first) as quicklook:
                values = quicklook.read(1)
                self.assertEqual((quicklook.height, quicklook.width), (3, 3))
                self.assertEqual(quicklook.dtypes, ("uint8",))
                self.assertTrue(np.all(np.isfinite(values)))
                self.assertGreater(int(values.max()), int(values.min()))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual((complex_path.stat(), _sha256(complex_path)), before)


if __name__ == "__main__":
    unittest.main()
