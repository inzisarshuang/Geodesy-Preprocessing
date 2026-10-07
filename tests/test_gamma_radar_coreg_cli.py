"""Tests for the GAMMA radar-grid coregistration command line."""

from __future__ import annotations

import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid import coreg_cli
from geodesy_preprocessing.sar.rslc_registration.common import RegistrationResult


PARAMETERS = """range_samples: 100
azimuth_lines: 80
range_pixel_spacing: 2.0 m
azimuth_pixel_spacing: 3.0 m
center_frequency: 1.2575e9 Hz
"""


class GammaRadarCoregCliTests(unittest.TestCase):
    """Expose dry-run planning, named cases, and stable process exit codes."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        project = self.root / "study"
        for date in ("20240229", "20240312"):
            scene = project / "SLC/HH_SLC" / date
            scene.mkdir(parents=True)
            (scene / f"{date}.slc").write_bytes(b"slc")
            (scene / f"{date}.slc.par").write_text(PARAMETERS, encoding="ascii")
        (project / "images").write_text("20240229\n20240312\n", encoding="ascii")
        geometry = project / "GEOMETRY"
        geometry.mkdir()
        (geometry / "20240229.HH.wgs84.hgt").write_bytes(b"height")
        (geometry / "reference_geometry.json").write_text(
            json.dumps(
                {
                    "reference_date": "20240229",
                    "primary_polarization": "HH",
                    "outputs": ["20240229.HH.wgs84.hgt"],
                }
            )
        )
        self.output = project / "RSLC"
        self.config = self.root / "coreg.cfg"
        self.config.write_text(
            """[project]
root = study
[case:main]
sensor = nisar
frequency = A
track = 87
frame = 22
orbit_direction = descending
reference_date = 20240229
primary_polarization = HH
selected_polarizations = HH
slc_dir = SLC
images_file = images
reference_geometry = GEOMETRY/reference_geometry.json
output_dir = RSLC
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dry_run_prints_complete_plan_without_mutation(self) -> None:
        terminal = io.StringIO()
        with redirect_stdout(terminal):
            code = coreg_cli.main(
                ["--config", str(self.config), "--case", "main", "--dry-run"]
            )
        payload = json.loads(terminal.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["sensor"], "nisar")
        self.assertEqual(payload["reference_date"], "20240229")
        self.assertEqual(payload["dates"], ["20240229", "20240312"])
        self.assertEqual(payload["frequency"], {"label": "A", "center_hz": 1.2575e9})
        self.assertEqual(payload["primary_polarization"], "HH")
        self.assertEqual(payload["selected_polarizations"], ["HH"])
        self.assertEqual(payload["refinement"], "standard")
        self.assertEqual(payload["output_dir"], str(self.output))
        self.assertFalse(self.output.exists())

    def test_formal_run_and_error_exit_codes(self) -> None:
        result = RegistrationResult(
            self.output / "registered_stack.json",
            "20240229",
            ("20240229", "20240312"),
            self.output / "quality.json",
            self.output,
        )
        with patch.object(coreg_cli, "run_gamma_radar_coreg", return_value=result) as run:
            self.assertEqual(coreg_cli.main(["--config", str(self.config)]), 0)
            self.assertEqual(run.call_count, 1)
        with patch.object(coreg_cli, "run_gamma_radar_coreg", side_effect=ValueError("bad")):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(coreg_cli.main(["--config", str(self.config)]), 1)
        with patch.object(coreg_cli, "run_gamma_radar_coreg", side_effect=KeyboardInterrupt):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(coreg_cli.main(["--config", str(self.config)]), 130)
        self.config.write_text("[project]\nroot = study\n", encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(coreg_cli.main(["--config", str(self.config)]), 2)

    def test_entry_point_and_namespace_are_backend_qualified(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        self.assertEqual(
            scripts["geodesy-pre-gamma-sar-coreg"],
            "geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_cli:main",
        )
        from geodesy_preprocessing.sar.rslc_registration.gamma import radar_grid

        self.assertTrue(callable(radar_grid.build_gamma_radar_coreg_plan))
        self.assertTrue(callable(radar_grid.run_gamma_radar_coreg))


if __name__ == "__main__":
    unittest.main()
