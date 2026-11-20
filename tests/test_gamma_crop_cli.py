"""Tests for the installed GAMMA registered-stack crop command."""

from __future__ import annotations

import io
import tempfile
import tomllib
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

    def test_help_documents_config_case_and_dry_run_without_force_option(self) -> None:
        terminal = io.StringIO()
        with self.assertRaises(SystemExit) as stopped, redirect_stdout(terminal):
            main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        for option in ("--config", "--case", "--dry-run"):
            self.assertIn(option, terminal.getvalue())
        self.assertNotIn("--force", terminal.getvalue())

    def test_common_crop_command_has_no_data_type_suffix(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        self.assertEqual(
            scripts["geodesy-pre-gamma-rslc-crop"],
            "geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.cli:main",
        )
        self.assertNotIn("geodesy-pre-gamma-sar-rslc-crop", scripts)

    def test_dry_run_overwrites_inspection_products(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, _ = crop_plan_fixtures.GammaCropPlanTests()._project(Path(directory))
            incomplete_output = config.parents[2] / "RAW/RSLC_CROP"
            incomplete_output.mkdir()
            (incomplete_output / "stale").write_text("partial")
            terminal = io.StringIO()
            with mock.patch(
                "geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.cli.build_crop_geometry",
                side_effect=crop_runner_fixtures.fake_geometry,
            ), mock.patch(
                "geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.cli.run_crop_stack",
                side_effect=AssertionError("dry-run must not crop RSLC"),
            ), redirect_stdout(terminal):
                self.assertEqual(
                    main(["--config", str(config), "--case", "main", "--dry-run"]),
                    0,
                )
                self.assertEqual(
                    main(["--config", str(config), "--case", "main", "--dry-run"]),
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
            self.assertIn("裁剪检查完成", terminal.getvalue())
            self.assertEqual((incomplete_output / "stale").read_text(), "partial")

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
                    main(["--config", str(config), "--case", "main"]),
                    0,
                )
            self.assertNotIn("force", run.call_args.kwargs)

            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(
                    main(["--config", str(Path(directory) / "missing.cfg"), "--case", "main"]),
                    2,
                )
            self.assertIn("ERROR", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
