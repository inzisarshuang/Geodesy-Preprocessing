"""Tests for aligned GAMMA RSLC crop-window derivation."""

from __future__ import annotations

import unittest

import numpy as np

from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.window import (
    PixelBounds,
    align_crop_window,
    window_from_radar_mask,
)


class GammaCropWindowTests(unittest.TestCase):
    """Align radar-mask bounds outward and protect source image boundaries."""

    def test_mask_bbox_is_scaled_to_one_look_rslc_grid(self) -> None:
        mask = np.zeros((4, 5), dtype=np.uint8)
        mask[1:3, 1:4] = 1

        window = window_from_radar_mask(
            mask,
            range_looks=3,
            azimuth_looks=2,
            rslc_width=15,
            rslc_nlines=8,
        )

        self.assertEqual((window.roff, window.nr, window.loff, window.nl), (3, 9, 2, 4))
        self.assertEqual(window.warnings, ())

    def test_negative_and_right_lower_bounds_are_clamped_with_warnings(self) -> None:
        negative = align_crop_window(
            PixelBounds(-1, -2, 4, 3),
            range_looks=2,
            azimuth_looks=2,
            rslc_width=12,
            rslc_nlines=10,
        )
        edge = align_crop_window(
            PixelBounds(3, 2, 7, 6),
            range_looks=2,
            azimuth_looks=2,
            rslc_width=12,
            rslc_nlines=10,
        )

        self.assertEqual((negative.roff, negative.nr, negative.loff, negative.nl), (0, 8, 0, 6))
        self.assertIn("left", negative.warnings)
        self.assertIn("top", negative.warnings)
        self.assertEqual((edge.roff, edge.nr, edge.loff, edge.nl), (6, 6, 4, 6))
        self.assertIn("right", edge.warnings)
        self.assertIn("bottom", edge.warnings)

    def test_empty_mask_and_zero_clamped_window_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            window_from_radar_mask(
                np.zeros((3, 4), dtype=np.uint8),
                range_looks=2,
                azimuth_looks=2,
                rslc_width=8,
                rslc_nlines=6,
            )
        with self.assertRaisesRegex(ValueError, "zero"):
            align_crop_window(
                PixelBounds(7, 0, 8, 1),
                range_looks=2,
                azimuth_looks=2,
                rslc_width=12,
                rslc_nlines=10,
            )


if __name__ == "__main__":
    unittest.main()
