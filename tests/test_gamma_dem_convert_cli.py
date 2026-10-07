"""Tests for the standalone GAMMA DEM conversion command."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.dem.conversion.gamma.cli import main
from geodesy_preprocessing.dem.conversion.gamma.config import (
    GammaDemConvertCase,
    load_gamma_dem_convert_config,
)
from geodesy_preprocessing.dem.conversion.gamma.srtm2dem import (
    run_gamma_dem_convert,
)


class GammaDemConvertCliTests(unittest.TestCase):
    """Pin strict configuration and safe restart behavior for ``srtm2dem``."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        (self.project / "INPUT/DEM").mkdir(parents=True)
        (self.project / "WORK/GAMMA/DEM").mkdir(parents=True)
        (self.project / "INPUT/DEM/dem.tif").write_bytes(b"TIFF")
        self.config = self.root / "gamma_dem_convert.cfg"

    def write_config(self, extra: str = "") -> None:
        self.config.write_text(
            "[project]\n"
            "root = project\n\n"
            "[defaults]\n"
            "command = srtm2dem\n"
            "geoid_correction_flag = 0\n"
            "overwrite = false\n\n"
            "[case:main]\n"
            "input_geotiff = INPUT/DEM/dem.tif\n"
            "output_dem = WORK/GAMMA/DEM/srtm.dem\n"
            "output_par = WORK/GAMMA/DEM/srtm.dem.par\n"
            "log_file = WORK/GAMMA/DEM/srtm2dem.log\n"
            + extra,
            encoding="utf-8",
        )

    def test_loads_one_strict_case_and_resolves_project_paths(self) -> None:
        self.write_config()
        case = load_gamma_dem_convert_config(self.config)
        self.assertIsInstance(case, GammaDemConvertCase)
        self.assertEqual(case.name, "main")
        self.assertEqual(case.input_geotiff, self.project / "INPUT/DEM/dem.tif")
        self.assertEqual(case.output_dem, self.project / "WORK/GAMMA/DEM/srtm.dem")
        self.assertFalse(case.overwrite)

    def test_rejects_unknown_keys_multiple_cases_and_download_options(self) -> None:
        for extra, message in (
            ("api_key = forbidden\n", "unknown key"),
            (
                "\n[case:second]\n"
                "input_geotiff = INPUT/DEM/dem.tif\n"
                "output_dem = WORK/GAMMA/DEM/two.dem\n"
                "output_par = WORK/GAMMA/DEM/two.dem.par\n",
                "exactly one",
            ),
        ):
            with self.subTest(message=message):
                self.write_config(extra)
                with self.assertRaisesRegex(ValueError, message):
                    load_gamma_dem_convert_config(self.config)

    def test_dry_run_prints_exact_srtm2dem_plan_without_execution(self) -> None:
        self.write_config()
        stdout = io.StringIO()
        with (
            patch(
                "geodesy_preprocessing.dem.conversion.gamma.srtm2dem.convert_with_srtm2dem"
            ) as convert,
            redirect_stdout(stdout),
        ):
            result = run_gamma_dem_convert(
                load_gamma_dem_convert_config(self.config), dry_run=True
            )
        convert.assert_not_called()
        self.assertIsNone(result)
        self.assertIn(
            "srtm2dem "
            f"{self.project / 'INPUT/DEM/dem.tif'} "
            f"{self.project / 'WORK/GAMMA/DEM/srtm.dem'} "
            f"{self.project / 'WORK/GAMMA/DEM/srtm.dem.par'} 0",
            stdout.getvalue(),
        )

    def test_runner_forwards_the_exact_conversion_case(self) -> None:
        self.write_config()
        case = load_gamma_dem_convert_config(self.config)
        with patch(
            "geodesy_preprocessing.dem.conversion.gamma.srtm2dem.convert_with_srtm2dem",
            return_value={"status": "complete"},
        ) as convert:
            result = run_gamma_dem_convert(case)
        self.assertEqual(result, {"status": "complete"})
        self.assertEqual(
            convert.call_args.kwargs,
            {
                "input_geotiff": case.input_geotiff,
                "output_dem": case.output_dem,
                "output_par": case.output_par,
                "command": "srtm2dem",
                "geoid_correction_flag": 0,
                "geoid_path": None,
                "gdal_bin_dir": None,
                "log_path": case.log_path,
                "overwrite": False,
            },
        )

    def test_complete_output_skips_and_partial_output_fails(self) -> None:
        self.write_config()
        case = load_gamma_dem_convert_config(self.config)
        case.output_dem.write_bytes(b"\0" * 24)
        case.output_par.write_text(
            "data_format: REAL*4\nwidth: 3\nnlines: 2\n", encoding="ascii"
        )
        assert case.log_path is not None
        case.log_path.write_text("complete", encoding="utf-8")
        with patch(
            "geodesy_preprocessing.dem.conversion.gamma.srtm2dem.convert_with_srtm2dem"
        ) as convert:
            result = run_gamma_dem_convert(case)
        convert.assert_not_called()
        self.assertEqual(result["status"], "skipped_complete")

        case.log_path.unlink()
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            run_gamma_dem_convert(case)

    def test_rejects_symlink_output_without_touching_target(self) -> None:
        self.write_config()
        case = load_gamma_dem_convert_config(self.config)
        target = self.root / "outside.dem"
        target.write_bytes(b"do not change")
        case.output_dem.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            run_gamma_dem_convert(case, dry_run=True)
        self.assertEqual(target.read_bytes(), b"do not change")

    def test_cli_reports_dry_run_and_ordinary_errors_without_traceback(self) -> None:
        self.write_config()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(["--config", str(self.config), "--dry-run"]), 0)
        self.assertIn("dry-run", stdout.getvalue())

        self.config.write_text("[project]\nroot = project\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(["--config", str(self.config)]), 1)
        self.assertIn("ERROR:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
