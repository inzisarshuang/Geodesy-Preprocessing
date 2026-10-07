"""Tests for isolated GAMMA DEM conversion."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.dem.conversion.gamma.srtm2dem import (
    convert_with_srtm2dem,
    validate_gamma_dem,
)


def fingerprint(*paths: Path) -> tuple[tuple[bytes, int], ...]:
    """Return bytes and modification times for output-preservation checks."""

    return tuple((path.read_bytes(), path.stat().st_mtime_ns) for path in paths)


class GammaDemTests(unittest.TestCase):
    """Verify GAMMA file contracts and all-or-nothing publication."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.input_tiff = self.root / "input.tif"
        self.input_tiff.write_bytes(b"input")
        self.output_dem = self.root / "srtm.dem"
        self.output_par = self.root / "srtm.dem.par"
        self.output_log = self.root / "srtm2dem.log"
        source = Path(__file__).parent / "fixtures" / "fake_gamma_command.py"
        self.command = self.root / "fake_srtm2dem"
        shutil.copy2(source, self.command)
        self.command.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def convert(self, mode: str, *, overwrite: bool = False):
        with patch.dict(os.environ, {"FAKE_SRTM_MODE": mode}, clear=False):
            return convert_with_srtm2dem(
                input_geotiff=self.input_tiff,
                output_dem=self.output_dem,
                output_par=self.output_par,
                command=str(self.command),
                log_path=self.output_log,
                overwrite=overwrite,
            )

    def test_real4_size_must_equal_width_times_lines_times_four(self) -> None:
        self.output_par.write_text(
            "data_format: REAL*4\nwidth: 3\nnlines: 2\n", encoding="ascii"
        )
        self.output_dem.write_bytes(b"\0" * 24)
        self.assertEqual(
            validate_gamma_dem(self.output_dem, self.output_par)["width"], 3
        )
        self.output_dem.write_bytes(b"\0" * 20)
        with self.assertRaisesRegex(RuntimeError, "size does not match"):
            validate_gamma_dem(self.output_dem, self.output_par)

    def test_rejects_non_real4_and_missing_parameter_fields(self) -> None:
        self.output_dem.write_bytes(b"\0" * 24)
        for content, message in (
            ("data_format: INTEGER*2\nwidth: 3\nnlines: 2\n", r"REAL\*4"),
            ("data_format: REAL*4\nnlines: 2\n", "width"),
            ("data_format: REAL*4\nwidth: 3\n", "nlines"),
        ):
            self.output_par.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaisesRegex(
                RuntimeError, message
            ):
                validate_gamma_dem(self.output_dem, self.output_par)

    def test_partial_gamma_run_publishes_nothing(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "both DEM"):
            self.convert("missing-par")
        self.assertFalse(self.output_dem.exists())
        self.assertFalse(self.output_par.exists())
        self.assertFalse(self.output_log.exists())

    def test_failed_overwrite_preserves_all_existing_outputs(self) -> None:
        self.output_dem.write_bytes(b"old-dem")
        self.output_par.write_bytes(b"old-par")
        self.output_log.write_bytes(b"old-log")
        before = fingerprint(self.output_dem, self.output_par, self.output_log)
        with self.assertRaisesRegex(RuntimeError, r"REAL\*4"):
            self.convert("bad-format", overwrite=True)
        self.assertEqual(
            fingerprint(self.output_dem, self.output_par, self.output_log), before
        )

    def test_existing_outputs_are_preserved_without_overwrite(self) -> None:
        self.output_dem.write_bytes(b"old-dem")
        self.output_par.write_bytes(b"old-par")
        self.output_log.write_bytes(b"old-log")
        before = fingerprint(self.output_dem, self.output_par, self.output_log)
        with self.assertRaises(FileExistsError):
            self.convert("success", overwrite=False)
        self.assertEqual(
            fingerprint(self.output_dem, self.output_par, self.output_log), before
        )

    def test_successful_overwrite_replaces_the_complete_output_group(self) -> None:
        self.output_dem.write_bytes(b"old-dem")
        self.output_par.write_bytes(b"old-par")
        self.output_log.write_bytes(b"old-log")
        metadata = self.convert("success", overwrite=True)
        self.assertEqual(metadata["data_format"], "REAL*4")
        self.assertEqual(self.output_dem.read_bytes(), b"\0" * 24)
        self.assertIn(b"data_format: REAL*4", self.output_par.read_bytes())
        self.assertIn(b"fake mode=success", self.output_log.read_bytes())

    def test_success_publishes_valid_outputs_and_metadata(self) -> None:
        metadata = self.convert("success")
        self.assertEqual(metadata["data_format"], "REAL*4")
        self.assertEqual((metadata["nlines"], metadata["width"]), (2, 3))
        self.assertEqual(self.output_dem.stat().st_size, 24)
        self.assertTrue(self.output_par.is_file())
        self.assertTrue(self.output_log.is_file())

    def test_geoid_flag_two_requires_an_existing_geoid(self) -> None:
        with self.assertRaisesRegex(ValueError, "geoid"):
            convert_with_srtm2dem(
                input_geotiff=self.input_tiff,
                output_dem=self.output_dem,
                output_par=self.output_par,
                command=str(self.command),
                geoid_correction_flag=2,
            )


if __name__ == "__main__":
    unittest.main()
