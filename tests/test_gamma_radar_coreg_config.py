"""Tests for strict GAMMA radar-grid coregistration configuration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_config import (
    load_gamma_radar_coreg_config,
    select_gamma_radar_coreg_case,
)


class GammaRadarCoregConfigTests(unittest.TestCase):
    """Validate stack identity, modes, polarizations, thresholds, and paths."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "study").mkdir()
        self.config = self.root / "coreg.cfg"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, extra: str = "", sensor: str = "nisar") -> None:
        self.config.write_text(
            f"""[project]
root = study
[defaults]
range_looks = 5
azimuth_looks = 3
refinement = standard
sar_mode = auto
warning_offset_stddev = 0.15
failure_offset_stddev = 0.30
warning_residual_offset = 0.20
failure_residual_offset = 0.50
offset_polynomial_parameters = 6
offset_range_estimates = 128
offset_azimuth_estimates = 128
offset_range_window = 256
offset_azimuth_window = 256
offset_oversampling = 1
offset_correlation_threshold = 0.35
refine_range_step = 128
refine_azimuth_step = 128
refine_range_window = 256
refine_azimuth_window = 256
refine_oversampling = 1
refine_correlation_threshold = 0.35
[case:main]
sensor = {sensor}
frequency = A
track = 87
frame = 22
orbit_direction = descending
reference_date = 20240229
primary_polarization = HH
selected_polarizations = HH, HV
slc_dir = SLC
images_file = images
reference_geometry = GEOMETRY/reference_geometry.json
output_dir = RSLC/LA
{extra}
""",
            encoding="utf-8",
        )

    def test_loads_case_and_derives_repeat_pass_mode(self) -> None:
        self._write()
        case = select_gamma_radar_coreg_case(
            load_gamma_radar_coreg_config(self.config), None
        )
        project = self.root / "study"
        self.assertEqual(case.sensor, "nisar")
        self.assertEqual(case.frequency, "A")
        self.assertEqual((case.track, case.frame, case.orbit_direction), ("87", "22", "DESCENDING"))
        self.assertEqual(case.selected_polarizations, ("HH", "HV"))
        self.assertEqual(case.primary_polarization, "HH")
        self.assertEqual(case.sar_mode, 1)
        self.assertEqual(case.refinement, "standard")
        self.assertEqual(case.slc_dir, project / "SLC")
        self.assertEqual(case.output_dir, project / "RSLC/LA")
        self.assertEqual(
            (
                case.offset_polynomial_parameters,
                case.offset_range_estimates,
                case.offset_azimuth_estimates,
                case.offset_range_window,
                case.offset_azimuth_window,
                case.offset_oversampling,
                case.offset_correlation_threshold,
                case.refine_range_step,
                case.refine_azimuth_step,
                case.refine_range_window,
                case.refine_azimuth_window,
                case.refine_oversampling,
                case.refine_correlation_threshold,
            ),
            (6, 128, 128, 256, 256, 1, 0.35, 128, 128, 256, 256, 1, 0.35),
        )

    def test_lt1_auto_mode_is_single_pass_and_explicit_mode_is_checked(self) -> None:
        self._write(sensor="lt1")
        self.assertEqual(load_gamma_radar_coreg_config(self.config)[0].sar_mode, 0)
        self._write(extra="sar_mode = 1", sensor="lt1")
        with self.assertRaisesRegex(ValueError, "conflicts with sensor"):
            load_gamma_radar_coreg_config(self.config)

    def test_rejects_invalid_sensor_frequency_modes_and_polarizations(self) -> None:
        invalid = {
            "sensor = sentinel1": "sensor must be one of",
            "frequency = A,B": "single frequency",
            "refinement = magic": "standard or offset_field",
            "primary_polarization = VV": "primary polarization must be selected",
            "selected_polarizations = HH, HH": "duplicate polarization",
            "failure_offset_stddev = 0.1": "failure.*greater than warning",
            "unknown = yes": "unknown key",
            "track = auto": "track.*positive integer",
            "orbit_direction = sideways": "orbit_direction",
        }
        for replacement, message in invalid.items():
            with self.subTest(replacement=replacement):
                self._write()
                key = replacement.split("=", 1)[0].strip()
                text = self.config.read_text(encoding="utf-8")
                lines = [line for line in text.splitlines() if not line.startswith(f"{key} =")]
                lines.append(replacement)
                self.config.write_text("\n".join(lines) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load_gamma_radar_coreg_config(self.config)

    def test_paths_must_stay_in_project_and_case_selection_is_explicit(self) -> None:
        self._write()
        text = self.config.read_text(encoding="utf-8").replace(
            "output_dir = RSLC/LA", "output_dir = ../outside"
        )
        self.config.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside project root"):
            load_gamma_radar_coreg_config(self.config)

    def test_rejects_invalid_offset_controls(self) -> None:
        invalid = {
            "offset_polynomial_parameters = 2": "offset_polynomial_parameters.*1, 3, 4 or 6",
            "offset_range_estimates = 0": "offset_range_estimates.*positive integer",
            "offset_range_window = 7": "offset_range_window.*at least 8",
            "offset_oversampling = 3": "offset_oversampling.*1, 2 or 4",
            "offset_correlation_threshold = 1.1": "offset_correlation_threshold.*between 0 and 1",
            "refine_range_step = 0": "refine_range_step.*positive integer",
            "refine_azimuth_window = 4": "refine_azimuth_window.*at least 8",
            "refine_oversampling = 8": "refine_oversampling.*1, 2 or 4",
            "refine_correlation_threshold = -0.1": "refine_correlation_threshold.*between 0 and 1",
        }
        for value, message in invalid.items():
            with self.subTest(value=value):
                self._write(extra=value)
                with self.assertRaisesRegex(ValueError, message):
                    load_gamma_radar_coreg_config(self.config)


if __name__ == "__main__":
    unittest.main()
