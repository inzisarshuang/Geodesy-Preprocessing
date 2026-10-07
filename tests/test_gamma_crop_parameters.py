"""Tests for GAMMA map and radar-grid parameter parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from affine import Affine

from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.parameters import (
    read_gamma_map_grid,
    read_gamma_radar_grid,
)


class GammaCropParameterTests(unittest.TestCase):
    """Parse the minimum fields needed to derive and validate crop windows."""

    def _write(self, directory: str, name: str, text: str) -> Path:
        path = Path(directory) / name
        path.write_text(text, encoding="ascii")
        return path

    def test_reads_eqa_map_grid_and_pixel_edge_transform(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                directory,
                "reference.utm.dem.par",
                """DEM_projection: EQA
EPSG: 4326
width: 4
nlines: 3
corner_lat: 40.0 decimal degrees
corner_lon: -5.0 decimal degrees
post_lat: -0.01 decimal degrees
post_lon: 0.02 decimal degrees
""",
            )
            grid = read_gamma_map_grid(path)

        self.assertEqual(grid.crs.to_epsg(), 4326)
        self.assertEqual((grid.width, grid.nlines), (4, 3))
        self.assertEqual(grid.transform, Affine(0.02, 0.0, -5.01, 0.0, -0.01, 40.005))

    def test_reads_utm_map_grid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                directory,
                "reference.dem.par",
                """DEM_projection: UTM
EPSG: 32630
width: 10
nlines: 20
corner_east: 500000 m
corner_north: 4500000 m
post_east: 30 m
post_north: -30 m
""",
            )
            grid = read_gamma_map_grid(path)

        self.assertEqual(grid.crs.to_epsg(), 32630)
        self.assertEqual(grid.transform, Affine(30.0, 0.0, 499985.0, 0.0, -30.0, 4500015.0))

    def test_reads_mli_and_rslc_radar_grids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mli = self._write(
                directory,
                "reference.mli.par",
                """range_samples: 6699
azimuth_lines: 2331
range_looks: 7
azimuth_looks: 2
image_format: FLOAT
""",
            )
            rslc = self._write(
                directory,
                "reference.rslc.par",
                """range_samples: 46894
azimuth_lines: 4663
range_looks: 1
azimuth_looks: 1
image_format: FCOMPLEX
polarization: VV
""",
            )
            mli_grid = read_gamma_radar_grid(mli)
            rslc_grid = read_gamma_radar_grid(rslc)

        self.assertEqual((mli_grid.width, mli_grid.nlines), (6699, 2331))
        self.assertEqual((mli_grid.range_looks, mli_grid.azimuth_looks), (7, 2))
        self.assertEqual(rslc_grid.image_format, "FCOMPLEX")
        self.assertEqual(rslc_grid.polarization, "VV")

    def test_missing_field_error_names_file_and_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "broken.par", "range_samples: 20\n")
            with self.assertRaisesRegex(ValueError, r"broken\.par.*azimuth_lines"):
                read_gamma_radar_grid(path)


if __name__ == "__main__":
    unittest.main()
