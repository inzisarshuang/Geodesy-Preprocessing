"""Tests for the installed GAMMA registered-stack crop command."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tests.test_gamma_crop_plan as crop_plan_fixtures
import tests.test_gamma_crop_runner as crop_runner_fixtures
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.cli import main


class GammaCropCliTests(unittest.TestCase):
    """Expose help, inspectable planning, full execution and stable errors."""

    def test_help_documents_config_case_plan_only_and_force(self) -> None:
        terminal = io.StringIO()
        with self.assertRaises(SystemExit) as stopped, redirect_stdout(terminal):
            main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        for option in ("--config", "--case", "--plan-only", "--force"):
            self.assertIn(option, terminal.getvalue())

    def test_plan_only_publishes_inspection_products_and_skips_identical_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, _ = crop_plan_fixtures.GammaCropPlanTests()._project(Path(directory))
            terminal = io.StringIO()
            with mock.patch(
                "geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.cli.build_crop_geometry",
                side_effect=crop_runner_fixtures.fake_geometry,
            ), mock.patch(
                "geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.cli.run_crop_stack",
                side_effect=AssertionError("plan-only must not crop RSLC"),
            ), redirect_stdout(terminal):
                self.assertEqual(
                    main(["--config", str(config), "--case", "main", "--plan-only"]),
                    0,
                )
                self.assertEqual(
                    main(["--config", str(config), "--case", "main", "--plan-only"]),
                    0,
                )

            plan_dir = config.parents[2] / "RAW/RSLC_CROP.plan"
            for relative in (
                "buffered_aoi.geojson",
                "aoi_mask_map.tif",
                "aoi_mask_radar.bmp",
                "crop_window.json",
                "quicklooks/aoi_mask_radar.bmp",
            ):
                self.assertTrue((plan_dir / relative).is_file(), relative)
            self.assertIn("已经完成", terminal.getvalue())

    def test_normal_mode_delegates_to_full_runner_and_reports_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, _ = crop_plan_fixtures.GammaCropPlanTests()._project(Path(directory))
            with mock.patch(
                "geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.cli.run_crop_stack",
                return_value=SimpleNamespace(
                    total=2, completed=2, skipped=0, output_dir=Path("result")
                ),
            ) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["--config", str(config), "--case", "main", "--force"]),
                    0,
                )
            self.assertTrue(run.call_args.kwargs["force"])

            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(
                    main(["--config", str(Path(directory) / "missing.cfg"), "--case", "main"]),
                    2,
                )
            self.assertIn("ERROR", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
