"""Tests for the installed DEM command."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.dem.cli import main
from geodesy_preprocessing.dem.cli import run_from_config


def fingerprint(*paths: Path) -> tuple[tuple[bytes, int], ...]:
    return tuple((path.read_bytes(), path.stat().st_mtime_ns) for path in paths)


class DemCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cfg = self.root / "opentopo_dem_download.cfg"
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.temporary.cleanup)

    def write_cfg(self, **updates: object) -> None:
        values: dict[str, object] = {
            "input_mode": "bbox",
            "west": -5,
            "south": 40,
            "east": -4.99,
            "north": 40.01,
            "buffer_km": 0,
            "dem_type": "COP30",
            "api_url": "https://portal.opentopography.org/API/globaldem",
            "api_key_env": "TEST_OT_KEY",
            "output_format": "GTiff",
            "output_dir": "output",
            "output_name": "dem.tif",
            "write_metadata": "false",
            "overwrite": "false",
            "run_srtm2dem": "false",
            "srtm2dem_output_dir": "output",
            "srtm2dem_dem_name": "srtm.dem",
            "srtm2dem_par_name": "srtm.dem.par",
            "srtm2dem_log_name": "srtm2dem.log",
        }
        values.update(updates)
        self.cfg.write_text(
            "".join(f"{key} = {value}\n" for key, value in values.items()),
            encoding="utf-8",
        )

    def invoke(self, arguments: list[str]) -> int:
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            return main(arguments)

    def test_dry_run_prints_plan_without_key_or_network(self) -> None:
        self.write_cfg()
        with patch("geodesy_preprocessing.dem.cli.download_dem") as download:
            code = self.invoke(["--config", str(self.cfg), "--dry-run"])
        self.assertEqual(code, 0)
        download.assert_not_called()
        output = self.stdout.getvalue() + self.stderr.getvalue()
        self.assertIn("dry-run", output)
        self.assertNotIn("secret-value", output)

    def test_dry_run_prints_complete_gamma_plan(self) -> None:
        geoid = self.root / "geoid.dem"
        geoid.write_bytes(b"geoid")
        gdal_bin = self.root / "gdal-bin"
        gdal_bin.mkdir()
        self.write_cfg(
            run_srtm2dem="true",
            srtm2dem_geoid_correction_flag=2,
            srtm2dem_geoid_file=geoid,
            srtm2dem_gdal_bin_dir=gdal_bin,
        )
        code = self.invoke(["--config", str(self.cfg), "--dry-run"])
        self.assertEqual(code, 0)
        output = self.stdout.getvalue()
        for expected in (
            str(self.root / "output" / "srtm.dem"),
            str(self.root / "output" / "srtm.dem.par"),
            "geoid correction: 2",
            str(geoid),
            str(gdal_bin),
        ):
            self.assertIn(expected, output)

    def test_existing_gamma_outputs_stop_before_download_and_are_preserved(self) -> None:
        self.write_cfg(run_srtm2dem="true")
        output = self.root / "output"
        output.mkdir()
        paths = [output / "srtm.dem", output / "srtm.dem.par", output / "srtm2dem.log"]
        for index, path in enumerate(paths):
            path.write_bytes(f"original-{index}".encode())
        before = fingerprint(*paths)
        with patch("geodesy_preprocessing.dem.cli.download_dem") as download:
            code = self.invoke(["--config", str(self.cfg)])
        self.assertEqual(code, 1)
        download.assert_not_called()
        self.assertEqual(fingerprint(*paths), before)

    def test_rejects_unknown_key_and_invalid_input_mode(self) -> None:
        self.write_cfg(typo=1)
        self.assertEqual(self.invoke(["-c", str(self.cfg), "--dry-run"]), 1)
        self.assertIn("unknown configuration", self.stderr.getvalue())
        self.stdout.seek(0); self.stdout.truncate()
        self.stderr.seek(0); self.stderr.truncate()
        self.write_cfg(input_mode="invented")
        self.assertEqual(self.invoke(["-c", str(self.cfg), "--dry-run"]), 1)
        self.assertIn("input_mode", self.stderr.getvalue())

    def test_rejects_untrusted_url_before_looking_up_key(self) -> None:
        self.write_cfg(api_url="https://example.com/globaldem")
        self.assertEqual(self.invoke(["-c", str(self.cfg)]), 1)
        self.assertIn("host", self.stderr.getvalue())
        self.assertNotIn("API key not found", self.stderr.getvalue())

    def test_geoid_flag_two_requires_existing_file(self) -> None:
        self.write_cfg(
            run_srtm2dem="true",
            srtm2dem_geoid_correction_flag=2,
            srtm2dem_geoid_file="missing_geoid.tif",
        )
        self.assertEqual(self.invoke(["-c", str(self.cfg), "--dry-run"]), 1)
        self.assertIn("geoid file not found", self.stderr.getvalue())

    def test_rejects_30m_request_over_area_limit(self) -> None:
        self.write_cfg(west=-20, south=10, east=20, north=50)
        self.assertEqual(self.invoke(["-c", str(self.cfg), "--dry-run"]), 1)
        self.assertIn("request limit", self.stderr.getvalue())

    def test_force_is_forwarded_to_download_and_gamma_conversion(self) -> None:
        self.write_cfg(run_srtm2dem="true", api_key="secret-value")
        output_dir = self.root / "output"
        output_dir.mkdir()
        dem = output_dir / "srtm.dem"
        par = output_dir / "srtm.dem.par"
        log = output_dir / "srtm2dem.log"
        for path in (dem, par, log):
            path.write_text("old", encoding="utf-8")

        def fake_download(**kwargs: object) -> Path:
            path = Path(kwargs["output_path"])
            path.write_bytes(b"TIFF")
            return path

        with (
            patch("geodesy_preprocessing.dem.cli.download_dem", side_effect=fake_download) as download,
            patch("geodesy_preprocessing.dem.cli.convert_with_srtm2dem", return_value={}) as convert,
        ):
            code = self.invoke(["-c", str(self.cfg), "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(download.call_args.kwargs["overwrite"])
        self.assertTrue(convert.call_args.kwargs["overwrite"])

    def test_ordinary_failure_prints_one_error_without_traceback(self) -> None:
        self.write_cfg(api_key="secret-value")
        with patch(
            "geodesy_preprocessing.dem.cli.download_dem",
            side_effect=RuntimeError("service unavailable"),
        ):
            code = self.invoke(["-c", str(self.cfg)])
        self.assertEqual(code, 1)
        error_lines = [line for line in self.stderr.getvalue().splitlines() if line]
        self.assertEqual(error_lines, ["ERROR: service unavailable"])
        self.assertNotIn("Traceback", self.stderr.getvalue())

    def test_combined_legacy_config_still_calls_download_then_gamma(self) -> None:
        self.write_cfg(run_srtm2dem="true", api_key="secret-value")
        cfg = {
            line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in self.cfg.read_text(encoding="utf-8").splitlines()
            if "=" in line
        }

        def fake_download(**kwargs: object) -> Path:
            path = Path(kwargs["output_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"TIFF")
            return path

        with (
            patch("geodesy_preprocessing.dem.cli.download_dem", side_effect=fake_download),
            patch(
                "geodesy_preprocessing.dem.cli.convert_with_srtm2dem",
                return_value={"data_format": "REAL*4"},
            ) as convert,
        ):
            result = run_from_config(cfg, config_path=self.cfg)
        self.assertEqual(result, self.root / "output/dem.tif")
        self.assertEqual(convert.call_args.kwargs["input_geotiff"], result)

    def test_existing_geotiff_is_reused_for_gamma_conversion(self) -> None:
        self.write_cfg(run_srtm2dem="true", srtm2dem_output_dir="gamma")
        output = self.root / "output/dem.tif"
        output.parent.mkdir()
        output.write_bytes(b"existing-geotiff")
        cfg = {
            line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in self.cfg.read_text(encoding="utf-8").splitlines()
            if "=" in line
        }

        with (
            patch("geodesy_preprocessing.dem.cli.validate_geotiff", return_value={}),
            patch("geodesy_preprocessing.dem.cli.resolve_api_key") as resolve_key,
            patch("geodesy_preprocessing.dem.cli.download_dem") as download,
            patch(
                "geodesy_preprocessing.dem.cli.convert_with_srtm2dem",
                return_value={"status": "complete"},
            ) as convert,
        ):
            result = run_from_config(cfg, config_path=self.cfg)

        self.assertEqual(result, output)
        resolve_key.assert_not_called()
        download.assert_not_called()
        self.assertEqual(
            convert.call_args.kwargs["output_dem"],
            self.root / "gamma/srtm.dem",
        )

    def test_complete_gamma_outputs_are_validated_and_skipped(self) -> None:
        self.write_cfg(run_srtm2dem="true", srtm2dem_output_dir="gamma")
        output = self.root / "output/dem.tif"
        output.parent.mkdir()
        output.write_bytes(b"existing-geotiff")
        gamma = self.root / "gamma"
        gamma.mkdir()
        (gamma / "srtm.dem").write_bytes(b"\0" * 24)
        (gamma / "srtm.dem.par").write_text(
            "data_format: REAL*4\nwidth: 3\nnlines: 2\n",
            encoding="ascii",
        )
        (gamma / "srtm2dem.log").write_text("complete", encoding="utf-8")
        cfg = {
            line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in self.cfg.read_text(encoding="utf-8").splitlines()
            if "=" in line
        }

        with (
            patch("geodesy_preprocessing.dem.cli.validate_geotiff", return_value={}),
            patch("geodesy_preprocessing.dem.cli.resolve_api_key") as resolve_key,
            patch("geodesy_preprocessing.dem.cli.download_dem") as download,
            patch("geodesy_preprocessing.dem.cli.convert_with_srtm2dem") as convert,
            redirect_stdout(self.stdout),
        ):
            result = run_from_config(cfg, config_path=self.cfg)

        self.assertEqual(result, output)
        resolve_key.assert_not_called()
        download.assert_not_called()
        convert.assert_not_called()
        self.assertIn("完整，跳过转换", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
