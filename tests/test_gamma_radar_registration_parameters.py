"""Tests for strict GAMMA radar-grid registration parameter models."""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.models import (
    GammaSlcAsset,
    GammaSlcGrid,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.parameters import (
    read_gamma_value,
    read_slc_grid,
)


VALID_PARAMETERS = """\
title: test scene
range_samples: 2048
azimuth_lines: 1024
range_pixel_spacing: 2.5 m
azimuth_pixel_spacing: 3.0 m
"""


class GammaRadarRegistrationParameterTests(unittest.TestCase):
    """Reject ambiguous parameters before any registration command runs."""

    def _parameter_file(self, directory: str, text: str = VALID_PARAMETERS) -> Path:
        path = Path(directory) / "scene.slc.par"
        path.write_text(text, encoding="ascii")
        return path

    def test_reads_exactly_one_keyword_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self._parameter_file(directory)
            self.assertEqual(read_gamma_value(source, "range_samples"), "2048")
            self.assertEqual(read_gamma_value(source, "title"), "test scene")

    def test_rejects_missing_duplicate_or_empty_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self._parameter_file(directory)
            with self.assertRaisesRegex(ValueError, "expected exactly one missing"):
                read_gamma_value(source, "missing")

            duplicate = self._parameter_file(
                directory,
                VALID_PARAMETERS + "range_samples: 4096\n",
            )
            with self.assertRaisesRegex(ValueError, "expected exactly one range_samples"):
                read_gamma_value(duplicate, "range_samples")

            empty = self._parameter_file(directory, "range_samples:\n")
            with self.assertRaisesRegex(ValueError, "empty range_samples"):
                read_gamma_value(empty, "range_samples")

    def test_reads_positive_finite_slc_grid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            grid = read_slc_grid(self._parameter_file(directory))

        self.assertEqual(
            grid,
            GammaSlcGrid(
                range_samples=2048,
                azimuth_lines=1024,
                range_spacing=2.5,
                azimuth_spacing=3.0,
            ),
        )

    def test_rejects_invalid_grid_numbers(self) -> None:
        replacements = {
            "range_samples: 2048": (
                "range_samples: 2.5",
                "range_samples: 0",
                "range_samples: text",
            ),
            "azimuth_lines: 1024": ("azimuth_lines: -1",),
            "range_pixel_spacing: 2.5 m": (
                "range_pixel_spacing: nan m",
                "range_pixel_spacing: 0 m",
            ),
            "azimuth_pixel_spacing: 3.0 m": (
                "azimuth_pixel_spacing: inf m",
                "azimuth_pixel_spacing: text m",
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            for original, invalid_values in replacements.items():
                for invalid in invalid_values:
                    with self.subTest(invalid=invalid):
                        source = self._parameter_file(
                            directory, VALID_PARAMETERS.replace(original, invalid)
                        )
                        with self.assertRaisesRegex(ValueError, "positive"):
                            read_slc_grid(source)

    def test_asset_is_immutable_and_validates_identity(self) -> None:
        asset = GammaSlcAsset(
            date="20240229",
            polarization="HH",
            data_path=Path("20240229.hh.slc"),
            parameter_path=Path("20240229.hh.slc.par"),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            asset.date = "20240301"  # type: ignore[misc]

        invalid_cases = (
            {"date": "20240230"},
            {"date": "2024-02-29"},
            {"polarization": "hh"},
            {"polarization": ""},
            {"data_path": "scene.slc"},
            {"parameter_path": "scene.slc.par"},
            {"parameter_path": Path("20240229.hh.slc")},
        )
        for replacement in invalid_cases:
            with self.subTest(replacement=replacement):
                values = {
                    "date": "20240229",
                    "polarization": "HH",
                    "data_path": Path("20240229.hh.slc"),
                    "parameter_path": Path("20240229.hh.slc.par"),
                }
                values.update(replacement)
                with self.assertRaises((TypeError, ValueError)):
                    GammaSlcAsset(**values)  # type: ignore[arg-type]

    def test_grid_model_rejects_nonpositive_and_boolean_values(self) -> None:
        valid = {
            "range_samples": 10,
            "azimuth_lines": 20,
            "range_spacing": 2.0,
            "azimuth_spacing": 3.0,
        }
        for field, value in (
            ("range_samples", True),
            ("range_samples", 0),
            ("azimuth_lines", -1),
            ("range_spacing", float("nan")),
            ("azimuth_spacing", float("inf")),
            ("azimuth_spacing", 0.0),
        ):
            with self.subTest(field=field, value=value):
                values = dict(valid)
                values[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    GammaSlcGrid(**values)


if __name__ == "__main__":
    unittest.main()
