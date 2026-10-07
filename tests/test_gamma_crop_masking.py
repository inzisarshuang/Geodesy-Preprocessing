"""Tests for KML mask coverage and GAMMA geocoding orchestration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

import tests.test_gamma_crop_plan as crop_plan_fixtures
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.config import load_crop_config
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.masking import (
    build_crop_geometry,
    validate_coverage,
)
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.plan import build_crop_plan


class FakeGammaRunner:
    """Materialize controlled GAMMA UCHAR outputs at the external-command boundary."""

    def __init__(self, valid_map: np.ndarray, radar_mask: np.ndarray) -> None:
        self.valid_map = valid_map
        self.radar_mask = radar_mask
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...]):
        self.commands.append(command)
        if Path(command[0]).name == "geocode_back":
            Path(command[4]).write_bytes(self.valid_map.astype(np.uint8).tobytes())
        elif Path(command[0]).name == "geocode":
            Path(command[4]).write_bytes(self.radar_mask.astype(np.uint8).tobytes())
        else:
            raise AssertionError(command)


class GammaCropMaskingTests(unittest.TestCase):
    """Measure map-grid coverage and produce inspectable crop artifacts."""

    def test_original_requires_full_coverage_and_buffer_uses_threshold(self) -> None:
        original = np.ones((1, 1000), dtype=np.uint8)
        buffered = np.ones((1, 1000), dtype=np.uint8)
        valid = np.ones((1, 1000), dtype=np.uint8)
        valid[0, -1] = 0
        with self.assertRaisesRegex(ValueError, "99.900%"):
            validate_coverage(original, buffered, valid, True, 0.95)

        original = np.zeros((1, 100), dtype=np.uint8)
        original[0, :50] = 1
        valid = np.zeros((1, 100), dtype=np.uint8)
        valid[0, :95] = 1
        report = validate_coverage(original, buffered[:1, :100], valid, True, 0.95)
        self.assertEqual(report.original_ratio, 1.0)
        self.assertEqual(report.buffered_ratio, 0.95)
        valid[0, 94] = 0
        with self.assertRaisesRegex(ValueError, "94.000%"):
            validate_coverage(original, buffered[:1, :100], valid, True, 0.95)

    def test_commands_and_artifacts_use_declared_map_and_mli_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, _ = crop_plan_fixtures.GammaCropPlanTests()._project(root)
            plan = build_crop_plan(load_crop_config(config_path, "main"))
            radar = np.zeros((3, 4), dtype=np.uint8)
            radar[1:3, 1:4] = 1
            runner = FakeGammaRunner(np.ones((3, 4), dtype=np.uint8), radar)
            artifacts = root / "artifacts"

            result = build_crop_geometry(plan, artifacts, runner=runner)

            self.assertEqual((result.window.roff, result.window.nr), (2, 6))
            self.assertEqual((result.window.loff, result.window.nl), (2, 4))
            self.assertEqual(result.coverage.original_ratio, 1.0)
            self.assertEqual(result.coverage.buffered_ratio, 1.0)
            back, forward = runner.commands
            self.assertEqual(Path(back[0]).name, "geocode_back")
            self.assertEqual(back[2], "4")
            self.assertEqual(back[5:9], ("4", "3", "0", "3"))
            self.assertEqual(Path(forward[0]).name, "geocode")
            self.assertEqual(forward[3], "4")
            self.assertEqual(forward[5:9], ("4", "3", "1", "3"))
            for path in (
                result.buffered_geojson,
                result.map_mask_tif,
                result.radar_mask_bmp,
            ):
                self.assertTrue(path.is_file() and path.stat().st_size > 0, path)


if __name__ == "__main__":
    unittest.main()
