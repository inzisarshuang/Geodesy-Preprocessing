"""End-to-end contract checks for distinct NISAR RSLC and GSLC preprocessing routes."""

from __future__ import annotations

import configparser
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _config(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / "config_examples" / name, encoding="utf-8")
    return parser


class NisarGslcWorkflowContractTests(unittest.TestCase):
    """Keep native GSLC separate from the GAMMA RSLC route and Analysis."""

    def test_both_nisar_routes_are_discoverable_with_unambiguous_templates(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        self.assertIn("geodesy-pre-sar-nisar-acquire", scripts)
        self.assertIn("geodesy-pre-sar-nisar-gslc", scripts)
        for template in (
            "nisar_rslc_download.cfg",
            "nisar_gslc_download.cfg",
            "nisar_gslc_stack.cfg",
        ):
            self.assertTrue((ROOT / "config_examples" / template).is_file(), template)
        self.assertFalse((ROOT / "config_examples/nisar_download.cfg").exists())
        for current_guide in (
            ROOT / "README.md",
            ROOT / "config_examples/nisar_rslc_download.cfg",
            ROOT / "docs/workflows/nisar_rslc.md",
        ):
            self.assertNotIn("nisar_download.cfg", current_guide.read_text(encoding="utf-8"))

    def test_download_templates_feed_their_next_stage_without_path_guessing(self) -> None:
        rslc = _config("nisar_rslc_download.cfg")
        gslc = _config("nisar_gslc_download.cfg")
        gamma_slc = _config("gamma_sar_slc.cfg")
        gslc_stack = _config("nisar_gslc_stack.cfg")

        self.assertEqual(rslc["defaults"]["download_dir"], gamma_slc["case:nisar_la"]["raw_dir"])
        self.assertEqual(
            Path(gslc_stack["defaults"]["source_manifest"]).parent,
            Path(gslc["defaults"]["download_dir"]),
        )
        self.assertEqual(gslc["defaults"]["product_type"], "GSLC")
        self.assertEqual(gslc_stack["case:la"]["frequency"], "A")
        self.assertEqual(gslc_stack["case:lb"]["frequency"], "B")

    def test_native_gslc_implementation_contains_no_gamma_processing_command(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(
                (ROOT / "src/geodesy_preprocessing/sar/gslc_subsetting/nisar").glob("*.py")
            )
        )
        for command in ("par_NISAR_RSLC", "SLC_copy", "gc_map", "multi_look", "rasSLC"):
            with self.subTest(command=command):
                self.assertNotIn(command, source)

    def test_user_handoff_stops_at_preprocessing_stack(self) -> None:
        guide = (ROOT / "docs/workflows/nisar_gslc.md").read_text(encoding="utf-8")
        acceptance = (
            ROOT / "docs/validation/preprocessing_acceptance.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "geodesy-pre-sar-nisar-acquire",
            "geodesy-pre-sar-nisar-gslc",
            "--plan-only",
            "registered_stack.json",
            "Geodesy-Analysis",
            "GUNW",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, guide + acceptance)
        for forbidden in ("mintpy run", "baseline_select", "unwrap"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, guide.lower())


if __name__ == "__main__":
    unittest.main()
