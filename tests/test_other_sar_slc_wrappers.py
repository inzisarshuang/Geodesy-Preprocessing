"""Behavior tests for historical raw-to-SLC argument translation."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fixtures.other_sar import write_cosmo_product
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.legacy import (
    legacy_slc_case,
    legacy_slc_main,
)


FIXTURE = Path(__file__).parent / "fixtures/fake_other_sar_command.py"


class OtherSarLegacySlcTests(unittest.TestCase):
    """Preserve usage and translate every maintained positional interface."""

    def test_each_no_argument_entry_prints_its_historical_name(self) -> None:
        expected = {
            "alos2": "01-raw2slc_ALOS2",
            "csg": "01-raw2slc_CSG",
            "csks": "01-raw2slc_CSKS",
            "lt1": "01-raw2slc_LT1",
            "rsat2": "01-raw2slc_RSAT2",
            "saocom": "01-raw2slc_SAOCOM",
        }
        for sensor, script_name in expected.items():
            with self.subTest(sensor=sensor):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(legacy_slc_main(sensor, []), 0)
                self.assertIn(f"usage: {script_name}", output.getvalue())

    def test_positional_arguments_translate_to_validated_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "RAW"
            slc = root / "SLC"
            for sensor in ("alos2", "csg", "csks", "lt1", "rsat2"):
                with self.subTest(sensor=sensor):
                    case = legacy_slc_case(sensor, [str(raw), str(slc)], root)
                    self.assertEqual(case.sensor, sensor)
                    self.assertEqual(case.raw_dir, raw)
                    self.assertEqual(case.slc_dir, slc)
                    self.assertEqual(case.workflow_task, "PRE_OTHER_SAR_SLC")
            self.assertEqual(legacy_slc_case("alos2", [str(raw), str(slc)], root).polarizations, ())
            self.assertEqual(legacy_slc_case("saocom", [str(raw), str(slc), "aa"], root).polarizations, ("HH",))
            self.assertEqual(legacy_slc_case("saocom", [str(raw), str(slc), "aa-ab"], root).polarizations, ("HH", "HV"))
            with self.assertRaisesRegex(ValueError, "historical branch was incomplete"):
                legacy_slc_case("saocom", [str(raw), str(slc), "aa-bb"], root)

    def test_csg_legacy_entry_runs_installed_logic_through_path_converter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "RAW"
            raw.mkdir()
            write_cosmo_product(raw, sensor="csg", date="20260922")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            converter = fake_bin / "par_CSG_SLC"
            converter.write_text(
                f"#!/bin/sh\nexec {sys.executable} {FIXTURE} cosmo \"$@\"\n",
                encoding="utf-8",
            )
            converter.chmod(0o700)
            environment = {"PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", "")))}
            with patch.dict(os.environ, environment), chdir(root), redirect_stdout(io.StringIO()):
                self.assertEqual(legacy_slc_main("csg", [str(raw), str(root / "SLC")]), 0)
            self.assertTrue((root / "SLC/HH_SLC/CSG/20260922.slc").is_file())
            self.assertEqual((root / "images_CSG").read_text(), "20260922\n")


if __name__ == "__main__":
    unittest.main()
