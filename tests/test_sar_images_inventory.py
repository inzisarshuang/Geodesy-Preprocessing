"""Tests for deterministic backend-neutral SAR images inventories."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.inventory.common.images import write_images_inventory


class SarImagesInventoryTests(unittest.TestCase):
    """Write manifest order once and reject ambiguous dates."""

    def test_writes_dates_in_manifest_order_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_images_inventory(
                Path(directory) / "images", ("20240113", "20240101")
            )
            self.assertEqual(path.read_text(encoding="ascii"), "20240113\n20240101\n")

    def test_rejects_duplicate_or_invalid_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images"
            for dates in (("20240101", "20240101"), ("2024-01-01",), ()):
                with self.subTest(dates=dates), self.assertRaises(ValueError):
                    write_images_inventory(path, dates)


if __name__ == "__main__":
    unittest.main()
