"""Tests for mapping the historical S1-01 CFG to the canonical command."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.acquisition.sentinel1.legacy import legacy_main


class Sentinel1LegacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kml = self.root / "area.kml"
        self.kml.write_text("<kml/>")
        self.output = self.root / "RAW"
        self.config = self.root / "old.cfg"
        self.config.write_text(f"""
output_dir={self.output}
AOI_File={self.kml}
Start_time=20260901
End_time=20260930
relOrbit=154,74
flight_direction=Descending
S1_product_type=S1_Bursts
S1_beam_mode=IW
S1_polarization=VV
time_sleep=3
max_tries=5
download_threads=4
keep_burst_tif=True
earthdata_username=REAL-USER-MUST-BE-IGNORED
earthdata_password=REAL-PASS-MUST-BE-IGNORED
""")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_maps_old_names_dates_paths_and_actions_without_credentials(self) -> None:
        captured = {}

        def canonical(arguments):
            captured["arguments"] = arguments
            cfg = Path(arguments[1]).read_text()
            captured["cfg"] = cfg
            query_dir = self.output / "QUERY"
            query_dir.mkdir(parents=True, exist_ok=True)
            (query_dir / "images_154").write_text("20260908\n")
            return 0

        with mock.patch("geodesy_preprocessing.sar.acquisition.sentinel1.legacy.canonical_main", side_effect=canonical):
            code = legacy_main([str(self.config), "--query"])

        self.assertEqual(code, 0)
        self.assertEqual(captured["arguments"][-1], "--query")
        cfg = captured["cfg"]
        for marker in (
            "product_type = BURST", "start_date = 2026-09-01",
            "end_date = 2026-09-30", "tracks = 74,154",
            "orbit_direction = descending", f"download_dir = {self.output}",
            f"query_dir = {self.output / 'QUERY'}",
        ):
            self.assertIn(marker, cfg)
        self.assertNotIn("REAL-USER", cfg)
        self.assertNotIn("REAL-PASS", cfg)
        self.assertEqual((self.output / "images_154").read_text(), "20260908\n")

    def test_maps_each_product_and_defaults_to_query(self) -> None:
        expected = {"S1_Bursts": "BURST", "S1_SLC": "SLC", "S1_GRD": "GRD"}
        base_text = self.config.read_text()
        for old, new in expected.items():
            text = base_text.replace("S1_product_type=S1_Bursts", f"S1_product_type={old}")
            self.config.write_text(text)
            captured = []

            def canonical(arguments):
                captured.append((tuple(arguments), Path(arguments[1]).read_text()))
                return 0

            with self.subTest(product=old), mock.patch(
                "geodesy_preprocessing.sar.acquisition.sentinel1.legacy.canonical_main",
                side_effect=canonical,
            ):
                self.assertEqual(legacy_main([str(self.config)]), 0)
            self.assertEqual(captured[0][0][-1], "--query")
            self.assertIn(f"product_type = {new}", captured[0][1])

    def test_bbox_mode_and_invalid_old_values_are_handled_before_dispatch(self) -> None:
        text = self.config.read_text().replace(f"AOI_File={self.kml}", "AOI_File=NONE\nLONMIN=-5\nLATMIN=38\nLONMAX=-1\nLATMAX=42")
        self.config.write_text(text)
        seen = []
        with mock.patch("geodesy_preprocessing.sar.acquisition.sentinel1.legacy.canonical_main", side_effect=lambda arguments: seen.append(Path(arguments[1]).read_text()) or 0):
            self.assertEqual(legacy_main([str(self.config), "--download"]), 0)
        self.assertIn("bbox = -5,38,-1,42", seen[0])

        self.config.write_text(text.replace("S1_product_type=S1_Bursts", "S1_product_type=UNKNOWN"))
        with mock.patch("geodesy_preprocessing.sar.acquisition.sentinel1.legacy.canonical_main") as canonical:
            self.assertEqual(legacy_main([str(self.config), "--query"]), 2)
        canonical.assert_not_called()


if __name__ == "__main__":
    unittest.main()
