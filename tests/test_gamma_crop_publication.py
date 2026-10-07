"""Tests for crop signatures and atomic directory publication."""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests.test_gamma_crop_plan as crop_plan_fixtures
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.config import load_crop_config
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.plan import build_crop_plan
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.publication import (
    atomic_publish_crop,
    crop_input_signature,
)


class GammaCropPublicationTests(unittest.TestCase):
    """Identify exact inputs and replace targets without exposing partial results."""

    def test_signature_changes_with_kml_buffer_or_polarizations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path, _ = crop_plan_fixtures.GammaCropPlanTests()._project(Path(directory))
            config = load_crop_config(config_path, "main")
            plan = build_crop_plan(config)
            original = crop_input_signature(plan)
            changed_buffer = crop_input_signature(
                dataclasses.replace(plan, config=dataclasses.replace(config, buffer_km=6.0))
            )
            changed_pol = crop_input_signature(
                build_crop_plan(dataclasses.replace(config, polarizations=None))
            )
            config.kml_file.write_text(
                config.kml_file.read_text(encoding="utf-8").replace("-4.94", "-4.93"),
                encoding="utf-8",
            )
            changed_kml = crop_input_signature(plan)

        self.assertNotEqual(original, changed_buffer)
        self.assertNotEqual(original, changed_pol)
        self.assertNotEqual(original, changed_kml)

    def test_force_publication_replaces_complete_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result"
            output.mkdir()
            (output / "old").write_text("old")
            staging = root / "stage"
            staging.mkdir()
            (staging / "new").write_text("new")

            atomic_publish_crop(staging, output, force=True)

            self.assertFalse(staging.exists())
            self.assertFalse((output / "old").exists())
            self.assertEqual((output / "new").read_text(), "new")
            self.assertEqual(list(root.glob(".*.backup")), [])

    def test_failed_force_publication_restores_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result"
            output.mkdir()
            (output / "old").write_text("old")
            staging = root / "stage"
            staging.mkdir()
            (staging / "new").write_text("new")
            real_replace = __import__("os").replace

            def fail_staging(source, destination):
                if Path(source) == staging:
                    raise OSError("forced publish failure")
                return real_replace(source, destination)

            with mock.patch(
                "geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.publication.os.replace",
                side_effect=fail_staging,
            ), self.assertRaisesRegex(OSError, "forced"):
                atomic_publish_crop(staging, output, force=True)

            self.assertEqual((output / "old").read_text(), "old")
            self.assertTrue((staging / "new").is_file())


if __name__ == "__main__":
    unittest.main()
