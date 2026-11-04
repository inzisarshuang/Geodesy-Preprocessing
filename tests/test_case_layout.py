"""Tests for the canonical preprocessing case directory boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.common.case_layout import (
    PreprocessingCaseLayout,
    optional_case_layout,
)


class PreprocessingCaseLayoutTests(unittest.TestCase):
    def test_builds_s1_method_product_figure_and_log_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            layout = PreprocessingCaseLayout.bind(
                project,
                "S1/ASCENDING_128",
                "S1/ASCENDING_128/GAMMA",
            )

            self.assertEqual(layout.case_id, "S1/ASCENDING_128")
            self.assertEqual(
                layout.processing_root,
                project / "S1/ASCENDING_128/GAMMA",
            )
            self.assertEqual(layout.config_root, project / "config")
            self.assertEqual(layout.config("preprocessing"), project / "config/1preprocessing")
            self.assertEqual(
                layout.acquisition_manifest,
                project / "S1/ASCENDING_128/acquisition_manifest.json",
            )
            self.assertEqual(
                layout.product("SLC"), project / "S1/ASCENDING_128/GAMMA/SLC"
            )
            self.assertEqual(
                layout.product("GEOMETRY"),
                project / "S1/ASCENDING_128/GAMMA/RSLC/GEOCODING",
            )
            self.assertEqual(
                layout.product("RSLC"), project / "S1/ASCENDING_128/GAMMA/RSLC"
            )
            self.assertEqual(
                layout.product("RSLC_SUBSET"),
                project / "S1/ASCENDING_128/GAMMA/RSLC/SUBSET",
            )
            self.assertEqual(
                layout.figure("SLC"),
                project / "S1/ASCENDING_128/GAMMA/FIGURES/PRE/SLC",
            )
            self.assertEqual(
                layout.log("RSLC"),
                project / "S1/ASCENDING_128/GAMMA/LOG/RSLC",
            )

    def test_builds_saocom_method_root_without_inferring_method_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()

            layout = PreprocessingCaseLayout.bind(
                project,
                "SAOCOM/GUIZHOU",
                "SAOCOM/GUIZHOU/GAMMA_mb",
            )

            self.assertEqual(
                layout.processing_root,
                project / "SAOCOM/GUIZHOU/GAMMA_mb",
            )
            self.assertEqual(
                layout.product("RSLC_SUBSET"),
                project / "SAOCOM/GUIZHOU/GAMMA_mb/RSLC/SUBSET",
            )

    def test_rejects_unsafe_case_ids_and_noncanonical_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            for value in ("ASCENDING_128", "S1/A/B", "../outside", "/tmp/case"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    PreprocessingCaseLayout.bind(
                        project,
                        value,
                        "S1/ASCENDING_128/GAMMA",
                    )

            layout = PreprocessingCaseLayout.bind(
                project,
                "S1/ASCENDING_128",
                "S1/ASCENDING_128/GAMMA",
            )
            for output in (
                project / "S1/WORK/GAMMA/RSLC",
                project / "S1/DESCENDING_135/GAMMA/RSLC",
                project / "S1/ASCENDING_128/GAMMA/RSLC/custom",
            ):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, "canonical case path"):
                    layout.require_product(output, "RSLC", "output")

    def test_rejects_processing_root_outside_or_above_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            for value in (
                "S1/ASCENDING_128",
                "S1/DESCENDING_135/GAMMA",
                "SAOCOM/GUIZHOU/GAMMA_mb",
                "S1/ASCENDING_128/GAMMA/extra",
                "/tmp/GAMMA",
            ):
                with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, "processing_root"
                ):
                    PreprocessingCaseLayout.bind(
                        project,
                        "S1/ASCENDING_128",
                        value,
                    )

    def test_optional_layout_requires_project_and_processing_root_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            self.assertIsNone(optional_case_layout(project, {}))
            for values in (
                {"project": "S1/ASCENDING_128"},
                {"processing_root": "S1/ASCENDING_128/GAMMA"},
            ):
                with self.subTest(values=values), self.assertRaisesRegex(
                    ValueError, "must be provided together"
                ):
                    optional_case_layout(project, values)

            layout = optional_case_layout(
                project,
                {
                    "project": "S1/ASCENDING_128",
                    "processing_root": "S1/ASCENDING_128/GAMMA",
                },
            )
            self.assertIsNotNone(layout)
            assert layout is not None
            self.assertEqual(layout.case_id, "S1/ASCENDING_128")

    def test_resolves_symlinks_before_accepting_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            outside = workspace / "outside"
            project.mkdir()
            outside.mkdir()
            (project / "S1").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "outside project root"):
                PreprocessingCaseLayout.bind(
                    project,
                    "S1/ASCENDING_128",
                    "S1/ASCENDING_128/GAMMA",
                )


if __name__ == "__main__":
    unittest.main()
