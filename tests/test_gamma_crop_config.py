"""Tests for project-local GAMMA registered-stack crop configuration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.config import (
    load_crop_config,
)


class GammaCropConfigTests(unittest.TestCase):
    """Resolve cases, defaults, paths, polarizations and coverage thresholds."""

    def _config(self, root: Path, *, polarizations: str = "VV") -> Path:
        project = root / "study"
        directory = project / "config/preprocessing"
        directory.mkdir(parents=True)
        path = directory / "gamma_sar_rslc_crop.cfg"
        path.write_text(
            f"""[project]
root = ../..
[defaults]
polarizations = {polarizations}
require_full_aoi_coverage = true
min_buffer_coverage = 0.95
[case:main]
registered_stack = RAW/RSLC/registered_stack.json
geometry_dir = RAW/GEOMETRY
kml_file = AOI/main.kml
kml_placemark = target
output_dir = RAW/RSLC_CROP
[case:alternate]
registered_stack = RAW/RSLC/registered_stack.json
geometry_dir = RAW/GEOMETRY
kml_file = AOI/main.kml
kml_placemark =
output_dir = RAW/RSLC_CROP_ALT
buffer_km = 8
""",
            encoding="utf-8",
        )
        return path

    def test_resolves_project_paths_and_defaults_to_five_kilometres(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(Path(directory), polarizations="vv, vh")
            config = load_crop_config(path, "main")

        project = path.parents[2]
        self.assertEqual(config.name, "main")
        self.assertEqual(config.registered_stack, project / "RAW/RSLC/registered_stack.json")
        self.assertEqual(config.geometry_dir, project / "RAW/GEOMETRY")
        self.assertEqual(config.kml_file, project / "AOI/main.kml")
        self.assertEqual(config.output_dir, project / "RAW/RSLC_CROP")
        self.assertEqual(config.kml_placemark, "target")
        self.assertEqual(config.buffer_km, 5.0)
        self.assertEqual(config.polarizations, ("VV", "VH"))
        self.assertTrue(config.require_full_aoi_coverage)
        self.assertEqual(config.min_buffer_coverage, 0.95)

    def test_all_polarizations_and_case_override_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(Path(directory), polarizations="all")
            config = load_crop_config(path, "alternate")

        self.assertIsNone(config.polarizations)
        self.assertIsNone(config.kml_placemark)
        self.assertEqual(config.buffer_km, 8.0)

    def test_rejects_unknown_case_and_invalid_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(Path(directory))
            with self.assertRaisesRegex(ValueError, "unknown.*missing"):
                load_crop_config(path, "missing")
            text = path.read_text(encoding="utf-8").replace(
                "min_buffer_coverage = 0.95", "min_buffer_coverage = 1.2"
            )
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "min_buffer_coverage"):
                load_crop_config(path, "main")


if __name__ == "__main__":
    unittest.main()
