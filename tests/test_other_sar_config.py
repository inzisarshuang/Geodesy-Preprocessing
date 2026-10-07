"""Behavior tests for strict Other-SAR project configuration."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    load_gamma_radar_slc_config,
    load_other_sar_slc_config,
    select_other_sar_slc_case,
    validate_safe_date,
    validate_within_root,
)


class OtherSarConfigTests(unittest.TestCase):
    """Reject ambiguous configuration and resolve valid cases deterministically."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = self.root / "gamma_sar_slc.cfg"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, text: str) -> None:
        self.config.write_text(text, encoding="utf-8")

    def test_defaults_case_precedence_and_project_paths_are_resolved(self) -> None:
        self._write(
            """[project]
root = project

[defaults]
polarizations = hh, hv
range_looks = 8
azimuth_looks = 10
workflow_task = PRE_OTHER_SAR_SLC
multi_look_command = /opt/gamma/bin/multi_look

[case:ascending]
sensor = alos2
raw_dir = RAW
slc_dir = SLC
images_file = lists/images_alos2
azimuth_looks = 12
import_command = par_EORC_PALSAR
"""
        )

        cases = load_other_sar_slc_config(self.config)
        case = select_other_sar_slc_case(cases, None)

        self.assertEqual(case.name, "ascending")
        self.assertEqual(case.sensor, "alos2")
        self.assertEqual(case.raw_dir, self.project / "RAW")
        self.assertEqual(case.slc_dir, self.project / "SLC")
        self.assertEqual(case.images_file, self.project / "lists/images_alos2")
        self.assertEqual(case.polarizations, ("HH", "HV"))
        self.assertEqual((case.range_looks, case.azimuth_looks), (8, 12))
        self.assertEqual(case.commands["import_command"], "par_EORC_PALSAR")
        self.assertEqual(
            case.commands["multi_look_command"], "/opt/gamma/bin/multi_look"
        )
        with self.assertRaises(FrozenInstanceError):
            case.sensor = "csg"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            case.commands["import_command"] = "changed"  # type: ignore[index]

    def test_select_requires_a_name_for_multiple_cases(self) -> None:
        self._write(
            """[project]
root = project
[defaults]
range_looks = 10
azimuth_looks = 10
[case:a]
sensor = csg
raw_dir = RAW/a
slc_dir = SLC/a
[case:b]
sensor = csks
raw_dir = RAW/b
slc_dir = SLC/b
"""
        )
        cases = load_other_sar_slc_config(self.config)
        with self.assertRaisesRegex(ValueError, "--case is required"):
            select_other_sar_slc_case(cases, None)
        self.assertEqual(select_other_sar_slc_case(cases, "b").sensor, "csks")
        with self.assertRaisesRegex(ValueError, "unknown Other-SAR case"):
            select_other_sar_slc_case(cases, "missing")

    def test_only_six_sensor_names_are_accepted(self) -> None:
        accepted = ("alos2", "csg", "csks", "lt1", "rsat2", "saocom")
        for sensor in accepted:
            with self.subTest(sensor=sensor):
                self._write(
                    f"""[project]
root = project
[case:main]
sensor = {sensor}
raw_dir = RAW
slc_dir = SLC
"""
                )
                self.assertEqual(load_other_sar_slc_config(self.config)[0].sensor, sensor)
        self._write(
            """[project]
root = project
[case:main]
sensor = sentinel1
raw_dir = RAW
slc_dir = SLC
"""
        )
        with self.assertRaisesRegex(ValueError, "sensor must be one of"):
            load_other_sar_slc_config(self.config)

    def test_gamma_nisar_adapter_options_are_normalized_and_immutable(self) -> None:
        calibrations = {"none": "0", "beta0": "1", "sigma0": "2", "gamma0": "3"}
        for calibration, expected in calibrations.items():
            with self.subTest(calibration=calibration):
                self._write(
                    f"""[project]
root = project
[case:nisar_la]
sensor = nisar
raw_dir = INPUT/NISAR/RSLC
slc_dir = WORK/GAMMA/NISAR/LA/SLC
polarizations = HH, HV
adapter.band = l
adapter.frequency = a
adapter.radiometric_calibration = {calibration}
adapter.noise_subtraction = true
"""
                )
                case = load_gamma_radar_slc_config(self.config)[0]
                self.assertEqual(
                    dict(case.adapter_options),
                    {
                        "band": "L",
                        "frequency": "A",
                        "radiometric_calibration": expected,
                        "noise_subtraction": "1",
                    },
                )
                with self.assertRaises(TypeError):
                    case.adapter_options["band"] = "S"  # type: ignore[index]

    def test_adapter_options_are_nisar_only_and_strict(self) -> None:
        invalid = (
            ("alos2", "adapter.band = L", "only valid for sensor=nisar"),
            ("nisar", "adapter.band = X", "adapter.band"),
            ("nisar", "adapter.frequency = C", "adapter.frequency"),
            ("nisar", "adapter.radiometric_calibration = raw", "radiometric_calibration"),
            ("nisar", "adapter.noise_subtraction = maybe", "noise_subtraction"),
            ("nisar", "adapter.unknown = value", "unknown key"),
        )
        for sensor, fragment, message in invalid:
            with self.subTest(fragment=fragment):
                self._write(
                    f"""[project]
root = project
[case:main]
sensor = {sensor}
raw_dir = RAW
slc_dir = SLC
{fragment}
"""
                )
                with self.assertRaisesRegex(ValueError, message):
                    load_gamma_radar_slc_config(self.config)

    def test_unknown_keys_invalid_values_and_shell_syntax_are_rejected(self) -> None:
        invalid_fragments = {
            "typo = yes": "unknown key",
            "range_looks = 0": "positive integer",
            "polarizations = HH, QX": "unsupported polarization",
            "import_command = converter; touch bad": "shell operator",
        }
        for fragment, message in invalid_fragments.items():
            with self.subTest(fragment=fragment):
                self._write(
                    f"""[project]
root = project
[case:main]
sensor = alos2
raw_dir = RAW
slc_dir = SLC
{fragment}
"""
                )
                with self.assertRaisesRegex(ValueError, message):
                    load_other_sar_slc_config(self.config)

    def test_unsafe_date_and_paths_outside_root_are_rejected(self) -> None:
        self.assertEqual(validate_safe_date("20260922"), "20260922")
        for unsafe in ("2026-09-22", "../20260922", "20261301", "20260230"):
            with self.subTest(date=unsafe):
                with self.assertRaises(ValueError):
                    validate_safe_date(unsafe)
        self.assertEqual(
            validate_within_root(self.project / "RAW/scene", self.project, "raw"),
            self.project / "RAW/scene",
        )
        with self.assertRaisesRegex(ValueError, "outside project root"):
            validate_within_root(self.root / "outside", self.project, "raw")

        self._write(
            """[project]
root = project
[case:main]
sensor = alos2
raw_dir = ../outside
slc_dir = SLC
"""
        )
        with self.assertRaisesRegex(ValueError, "outside project root"):
            load_other_sar_slc_config(self.config)


if __name__ == "__main__":
    unittest.main()
