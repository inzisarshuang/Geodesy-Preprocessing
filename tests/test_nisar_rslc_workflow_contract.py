"""Contract tests for the documented NISAR LA preprocessing handoff."""

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


class NisarRslcWorkflowContractTests(unittest.TestCase):
    """Keep the copyable LA chain connected and stop it before Analysis."""

    def test_commands_and_templates_cover_the_whole_preprocessing_chain(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]

        expected = {
            "geodesy-pre-sar-nisar-acquire": "nisar_rslc_download.cfg",
            "geodesy-pre-gamma-sar-slc": "gamma_sar_slc.cfg",
            "geodesy-pre-gamma-sar-geo-ref": "gamma_sar_geo_ref.cfg",
            "geodesy-pre-gamma-sar-coreg": "gamma_sar_coreg.cfg",
            "geodesy-pre-gamma-sar-rslc-crop": "gamma_sar_rslc_crop.cfg",
        }
        self.assertTrue(expected.keys() <= scripts.keys())
        for template in expected.values():
            self.assertTrue((ROOT / "config_examples" / template).is_file(), template)

    def test_la_is_default_and_lb_requires_explicit_selection(self) -> None:
        acquisition = _config("nisar_rslc_download.cfg")
        slc = _config("gamma_sar_slc.cfg")

        self.assertEqual(acquisition["defaults"]["frequency_mode"], "main_only")
        self.assertEqual(slc["case:nisar_la"]["adapter.frequency"], "A")
        self.assertEqual(slc["case:nisar_lb"]["adapter.frequency"], "B")
        self.assertNotEqual("nisar_lb", "main")

    def test_la_outputs_are_the_next_stage_inputs_without_path_changes(self) -> None:
        acquisition = _config("nisar_rslc_download.cfg")
        slc = _config("gamma_sar_slc.cfg")
        geo_ref = _config("gamma_sar_geo_ref.cfg")
        coreg = _config("gamma_sar_coreg.cfg")
        crop = _config("gamma_sar_rslc_crop.cfg")

        self.assertEqual(
            acquisition["defaults"]["download_dir"],
            slc["case:nisar_la"]["raw_dir"],
        )
        self.assertEqual(
            slc["case:nisar_la"]["slc_dir"],
            geo_ref["case:nisar_la"]["slc_dir"],
        )
        self.assertEqual(
            slc["case:nisar_la"]["images_file"],
            geo_ref["case:nisar_la"]["images_file"],
        )
        self.assertEqual(
            geo_ref["case:nisar_la"]["output_dir"] + "/reference_geometry.json",
            coreg["case:nisar_la"]["reference_geometry"],
        )
        self.assertEqual(
            coreg["case:nisar_la"]["output_dir"] + "/registered_stack.json",
            crop["case:nisar_la"]["registered_stack"],
        )
        self.assertEqual(
            geo_ref["case:nisar_la"]["output_dir"],
            crop["case:nisar_la"]["geometry_dir"],
        )
        self.assertEqual(
            acquisition["case:main"]["kml_file"],
            crop["case:nisar_la"]["kml_file"],
        )

    def test_documented_sequence_stops_at_cropped_rslc(self) -> None:
        runbook = (
            ROOT / "docs/workflows/nisar_rslc.md"
        ).read_text(encoding="utf-8")
        commands = [
            "geodesy-pre-sar-nisar-acquire",
            "geodesy-pre-gamma-sar-slc",
            "geodesy-pre-gamma-sar-geo-ref",
            "geodesy-pre-gamma-sar-coreg",
            "geodesy-pre-gamma-sar-rslc-crop",
        ]
        offsets = [runbook.index(command) for command in commands]
        self.assertEqual(offsets, sorted(offsets))
        shell_blocks = "\n".join(
            part.split("```", 1)[0]
            for part in runbook.split("```bash")[1:]
        )
        for forbidden in ("baseline_select", "MintPy", "mintpy", "DS-InSAR"):
            self.assertNotIn(forbidden, shell_blocks)

    def test_optional_lb_uses_the_same_explicit_handoff_pattern(self) -> None:
        slc = _config("gamma_sar_slc.cfg")
        geo_ref = _config("gamma_sar_geo_ref.cfg")
        coreg = _config("gamma_sar_coreg.cfg")
        crop = _config("gamma_sar_rslc_crop.cfg")

        self.assertEqual(
            slc["case:nisar_lb"]["slc_dir"], geo_ref["case:nisar_lb"]["slc_dir"]
        )
        self.assertEqual(
            slc["case:nisar_lb"]["images_file"],
            geo_ref["case:nisar_lb"]["images_file"],
        )
        self.assertEqual(
            geo_ref["case:nisar_lb"]["output_dir"] + "/reference_geometry.json",
            coreg["case:nisar_lb"]["reference_geometry"],
        )
        self.assertEqual(
            coreg["case:nisar_lb"]["output_dir"] + "/registered_stack.json",
            crop["case:nisar_lb"]["registered_stack"],
        )
        self.assertEqual(
            geo_ref["case:nisar_lb"]["output_dir"],
            crop["case:nisar_lb"]["geometry_dir"],
        )


if __name__ == "__main__":
    unittest.main()
