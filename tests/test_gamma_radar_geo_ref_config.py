"""Tests for strict GAMMA radar-grid reference-geometry configuration."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_config import (
    load_gamma_radar_geo_ref_config,
    select_gamma_radar_geo_ref_case,
)


class GammaRadarGeoRefConfigTests(unittest.TestCase):
    """Resolve project-local paths and reject ambiguous unsafe configuration."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "study"
        self.project.mkdir()
        self.config = self.root / "gamma_sar_geo_ref.cfg"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, case_extra: str = "", defaults_extra: str = "") -> None:
        self.config.write_text(
            f"""[project]
root = study

[defaults]
range_looks = 5
azimuth_looks = 3
latitude_oversampling = auto
longitude_oversampling = auto
correlation_threshold = 0.25
polynomial_parameters = 3
{defaults_extra}

[case:main]
reference_date = 20240229
primary_polarization = hh
slc_dir = SLC
images_file = lists/images
dem = DEM/srtm.dem
dem_par = DEM/srtm.dem.par
output_dir = GEOMETRY/main
{case_extra}
""",
            encoding="utf-8",
        )

    def test_loads_required_paths_defaults_and_optional_parameters(self) -> None:
        self._write(
            defaults_extra="post_spacing = 20\nrange_window = 128\nazimuth_window = 64"
        )
        case = select_gamma_radar_geo_ref_case(
            load_gamma_radar_geo_ref_config(self.config), None
        )
        self.assertEqual(case.name, "main")
        self.assertEqual(case.reference_date, "20240229")
        self.assertEqual(case.primary_polarization, "HH")
        self.assertEqual(case.slc_dir, self.project / "SLC")
        self.assertEqual(case.images_file, self.project / "lists/images")
        self.assertEqual(case.dem, self.project / "DEM/srtm.dem")
        self.assertEqual(case.dem_par, self.project / "DEM/srtm.dem.par")
        self.assertEqual(case.output_dir, self.project / "GEOMETRY/main")
        self.assertEqual((case.range_looks, case.azimuth_looks), (5, 3))
        self.assertIsNone(case.latitude_oversampling)
        self.assertEqual(case.post_spacing, 20.0)
        self.assertEqual(case.correlation_threshold, 0.25)
        self.assertEqual(case.polynomial_parameters, 3)
        self.assertEqual((case.range_window, case.azimuth_window), (128, 64))
        with self.assertRaises(FrozenInstanceError):
            case.reference_date = "20240301"  # type: ignore[misc]

    def test_requires_every_identity_path_and_positive_look(self) -> None:
        required = (
            "reference_date",
            "primary_polarization",
            "slc_dir",
            "images_file",
            "dem",
            "dem_par",
            "output_dir",
        )
        for key in required:
            with self.subTest(key=key):
                self._write()
                text = self.config.read_text(encoding="utf-8")
                text = "\n".join(
                    line for line in text.splitlines() if not line.startswith(f"{key} =")
                )
                self.config.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "missing keys"):
                    load_gamma_radar_geo_ref_config(self.config)
        for value in ("0", "-1", "text"):
            with self.subTest(range_looks=value):
                self._write(case_extra=f"range_looks = {value}")
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    load_gamma_radar_geo_ref_config(self.config)

    def test_rejects_invalid_optional_numbers_unknown_keys_and_path_escape(self) -> None:
        invalid = {
            "latitude_oversampling = 0": "positive",
            "post_spacing = nan": "positive finite",
            "correlation_threshold = 1.1": "between 0 and 1",
            "polynomial_parameters = 2": "1, 3, 4 or 6",
            "range_offset_estimates = 0": "positive integer",
            "area_factor = inf": "positive finite",
            "mystery = yes": "unknown key",
        }
        for fragment, message in invalid.items():
            with self.subTest(fragment=fragment):
                self._write(case_extra=fragment)
                with self.assertRaisesRegex(ValueError, message):
                    load_gamma_radar_geo_ref_config(self.config)
        self._write()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "output_dir = GEOMETRY/main", "output_dir = ../outside"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "outside project root"):
            load_gamma_radar_geo_ref_config(self.config)

    def test_oversampling_pair_and_named_case_selection_are_explicit(self) -> None:
        self._write(case_extra="latitude_oversampling = 1.5")
        with self.assertRaisesRegex(ValueError, "both be auto or both numeric"):
            load_gamma_radar_geo_ref_config(self.config)

        self._write()
        text = self.config.read_text(encoding="utf-8")
        self.config.write_text(text + text[text.index("[case:main]"):].replace("main", "second", 1))
        cases = load_gamma_radar_geo_ref_config(self.config)
        with self.assertRaisesRegex(ValueError, "--case is required"):
            select_gamma_radar_geo_ref_case(cases, None)
        self.assertEqual(select_gamma_radar_geo_ref_case(cases, "second").name, "second")


if __name__ == "__main__":
    unittest.main()
