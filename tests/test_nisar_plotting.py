"""Tests for S1-style NISAR coverage review figures."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import colors
from matplotlib import pyplot as plt
from shapely import wkt
from shapely.geometry import box

from geodesy_preprocessing.sar.acquisition.nisar.models import (
    NisarGranule,
    NisarProductType,
)
from geodesy_preprocessing.sar.acquisition.nisar.plotting import (
    build_coverage_figure,
    save_coverage_figure,
)


def _granule(name: str, track: int, frame: int, west: float) -> NisarGranule:
    return NisarGranule(
        granule_id=name,
        product_type=NisarProductType.RSLC,
        acquisition_date="2026-01-01",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:01:00Z",
        track=track,
        frame=frame,
        orbit_direction="ASCENDING",
        footprint_wkt=wkt.dumps(box(west, 0.0, west + 2.0, 2.0), trim=True),
        size_bytes=1,
        url=f"https://example.test/{name}.h5",
        frequencies=("A",),
        polarizations=("HH",),
    )


class NisarPlottingTests(unittest.TestCase):
    """Keep NISAR review figures consistent and useful for human selection."""

    def setUp(self) -> None:
        self.aoi = box(0.5, 0.5, 2.5, 1.5)
        self.granules = (
            _granule("track-8-a", 8, 81, 0.0),
            _granule("track-3-a", 3, 31, 1.0),
            _granule("track-8-b", 8, 82, 2.0),
        )

    @staticmethod
    def _track_colors(figure) -> dict[str, tuple[float, float, float, float]]:
        axes = figure.axes[0]
        return {
            patch.get_label(): tuple(patch.get_edgecolor())
            for patch in axes.patches
            if patch.get_label().startswith("Track ")
        }

    def test_figure_has_red_aoi_stable_track_colors_labels_grid_and_counts(self) -> None:
        first = build_coverage_figure(self.granules, self.aoi, "NISAR all results")
        second = build_coverage_figure(tuple(reversed(self.granules)), self.aoi, "repeat")
        try:
            axes = first.axes[0]
            self.assertEqual(axes.get_xlabel(), "Longitude (degrees)")
            self.assertEqual(axes.get_ylabel(), "Latitude (degrees)")
            self.assertIn("3 products", axes.get_title())
            self.assertIn("2 tracks", axes.get_title())
            self.assertTrue(any(line.get_visible() for line in axes.get_xgridlines()))
            labels = axes.get_legend_handles_labels()[1]
            self.assertIn("AOI", labels)
            self.assertIn("Track 3: 1 product, frame 31", labels)
            self.assertIn("Track 8: 2 products, frames 81,82", labels)
            aoi_patch = next(patch for patch in axes.patches if patch.get_label() == "AOI")
            self.assertEqual(colors.to_hex(aoi_patch.get_edgecolor()), "#ff0000")
            self.assertEqual(self._track_colors(first), self._track_colors(second))
        finally:
            plt.close(first)
            plt.close(second)

    def test_saves_all_and_selected_png_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            all_path = root / "NISAR_RSLC_footprints_all.png"
            selected_path = root / "NISAR_RSLC_footprints_selected.png"

            self.assertEqual(
                save_coverage_figure(self.granules, self.aoi, all_path, "All"), all_path
            )
            self.assertEqual(
                save_coverage_figure(self.granules[:1], self.aoi, selected_path, "Selected"),
                selected_path,
            )

            self.assertGreater(all_path.stat().st_size, 1000)
            self.assertGreater(selected_path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
