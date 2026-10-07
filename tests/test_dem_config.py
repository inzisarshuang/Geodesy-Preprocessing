"""Tests for sectionless DEM project configuration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.dem.config import (
    ALLOWED_CONFIG_KEYS,
    cfg_bool,
    cfg_float,
    cfg_int,
    load_config,
    resolve_path,
    validate_config_keys,
)


class DemConfigTests(unittest.TestCase):
    """Verify strict parsing and paths relative to the configuration file."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()
        self.area = self.root / "area.kml"
        self.area.write_text("kml", encoding="utf-8")
        self.config_path = self.config_dir / "opentopo_dem_download.cfg"
        self.config_path.write_text(
            "# comment\ninput_mode = kml\nkml_file = ../area.kml\nretries = 4\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_comments_and_resolves_paths_from_cfg_directory(self) -> None:
        cfg = load_config(self.config_path)
        self.assertEqual(cfg["input_mode"], "kml")
        self.assertEqual(
            resolve_path("../area.kml", self.config_path.parent), self.area
        )

    def test_unknown_keys_and_invalid_typed_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_config_keys(
                {"input_mode": "bbox", "typo": "1"},
                ALLOWED_CONFIG_KEYS,
                self.config_path,
            )
        with self.assertRaisesRegex(ValueError, "integer"):
            cfg_int({"retries": "many"}, "retries", 4)
        with self.assertRaisesRegex(ValueError, "number"):
            cfg_float({"buffer_km": "wide"}, "buffer_km", 0.0)
        with self.assertRaisesRegex(ValueError, "boolean"):
            cfg_bool({"overwrite": "sometimes"}, "overwrite", False)

    def test_duplicate_keys_and_lines_without_equals_are_rejected(self) -> None:
        self.config_path.write_text(
            "input_mode=kml\ninput_mode=bbox\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_config(self.config_path)
        self.config_path.write_text("input_mode kml\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "key=value"):
            load_config(self.config_path)


if __name__ == "__main__":
    unittest.main()
