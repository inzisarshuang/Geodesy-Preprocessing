"""Tests for the GAMMA radar-grid reference-geometry command line."""

from __future__ import annotations

import io
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid import geo_ref_cli
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_runner import (
    GammaRadarGeoRefResult,
)


PARAMETERS = """range_samples: 100
azimuth_lines: 80
range_pixel_spacing: 2.0 m
azimuth_pixel_spacing: 3.0 m
"""


class GammaRadarGeoRefCliTests(unittest.TestCase):
    """Expose config/case/dry-run operation and stable exit codes."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        project = self.root / "study"
        scene = project / "SLC/HH_SLC/20240229"
        scene.mkdir(parents=True)
        (scene / "20240229.slc").write_bytes(b"slc")
        (scene / "20240229.slc.par").write_text(PARAMETERS, encoding="ascii")
        (project / "images").write_text("20240229\n", encoding="ascii")
        (project / "DEM").mkdir()
        (project / "DEM/srtm.dem").write_bytes(b"dem")
        (project / "DEM/srtm.dem.par").write_text("width: 10\n", encoding="ascii")
        self.output = project / "GEOMETRY"
        self.config = self.root / "geo.cfg"
        self.config.write_text(
            """[project]
root = study
[defaults]
range_looks = 5
azimuth_looks = 3
[case:main]
reference_date = 20240229
primary_polarization = HH
slc_dir = SLC
images_file = images
dem = DEM/srtm.dem
dem_par = DEM/srtm.dem.par
output_dir = GEOMETRY
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dry_run_prints_plan_without_creating_output(self) -> None:
        terminal = io.StringIO()
        with redirect_stdout(terminal):
            code = geo_ref_cli.main(
                ["--config", str(self.config), "--case", "main", "--dry-run"]
            )
        self.assertEqual(code, 0)
        self.assertIn("20240229", terminal.getvalue())
        self.assertIn(str(self.output), terminal.getvalue())
        self.assertFalse(self.output.exists())

    def test_formal_run_delegates_selected_plan(self) -> None:
        result = GammaRadarGeoRefResult(
            status="complete",
            output_dir=self.output,
            manifest=self.output / "reference_geometry.json",
        )
        with patch.object(
            geo_ref_cli, "run_gamma_radar_geo_ref", return_value=result
        ) as run:
            code = geo_ref_cli.main(["--config", str(self.config), "--case", "main"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0].case.name, "main")

    def test_invalid_configuration_returns_two(self) -> None:
        self.config.write_text("[project]\nroot = study\n", encoding="utf-8")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = geo_ref_cli.main(["--config", str(self.config)])
        self.assertEqual(code, 2)
        self.assertIn("ERROR", errors.getvalue())

    def test_entry_point_is_backend_qualified(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        self.assertEqual(
            scripts["geodesy-pre-gamma-sar-geo-ref"],
            "geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_cli:main",
        )


if __name__ == "__main__":
    unittest.main()
