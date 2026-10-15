"""Tests for the canonical preprocessing case directory boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.common.case_layout import PreprocessingCaseLayout


class PreprocessingCaseLayoutTests(unittest.TestCase):
    def test_builds_canonical_preprocessing_and_log_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()

            layout = PreprocessingCaseLayout.bind(project, "S1/ASCENDING_128")

            self.assertEqual(layout.case_id, "S1/ASCENDING_128")
            self.assertEqual(layout.case_root, project / "S1/ASCENDING_128")
            self.assertEqual(layout.config_root, project / "S1/ASCENDING_128/config")
            self.assertEqual(
                layout.config("preprocessing"),
                project / "S1/ASCENDING_128/config/preprocessing",
            )
            self.assertEqual(layout.pre_root, project / "S1/ASCENDING_128/PRE")
            self.assertEqual(
                layout.acquisition_manifest,
                project / "S1/ASCENDING_128/PRE/acquisition_manifest.json",
            )
            self.assertEqual(layout.analysis_root, project / "S1/ASCENDING_128/ANALYSIS")
            self.assertEqual(
                layout.visualization_root,
                project / "S1/ASCENDING_128/VISUALIZATION",
            )
            self.assertEqual(layout.product("SLC"), project / "S1/ASCENDING_128/PRE/GAMMA/SLC")
            self.assertEqual(layout.product("GEOMETRY"), project / "S1/ASCENDING_128/PRE/GAMMA/GEOMETRY")
            self.assertEqual(layout.product("RSLC"), project / "S1/ASCENDING_128/PRE/GAMMA/RSLC")
            self.assertEqual(layout.product("RSLC_SUBSET"), project / "S1/ASCENDING_128/PRE/GAMMA/RSLC_SUBSET")
            self.assertEqual(layout.log_root, project / "S1/ASCENDING_128/LOG/PRE/GAMMA")

    def test_rejects_unknown_case_config_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            layout = PreprocessingCaseLayout.bind(project, "S1/ASCENDING_128")

            with self.assertRaisesRegex(ValueError, "unknown case config stage"):
                layout.config("temporary")

    def test_requires_two_component_project_relative_case_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()

            for value in ("ASCENDING_128", "S1/A/B", "../outside", "/tmp/case"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    PreprocessingCaseLayout.bind(project, value)

    def test_rejects_noncanonical_or_cross_case_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            layout = PreprocessingCaseLayout.bind(project, "S1/ASCENDING_128")

            expected = project / "S1/ASCENDING_128/PRE/GAMMA/RSLC"
            self.assertEqual(layout.require_product(expected, "RSLC", "output"), expected)
            for output in (
                project / "S1/WORK/GAMMA/ascending/RSLC",
                project / "S1/DESCENDING_135/PRE/GAMMA/RSLC",
                project / "S1/ASCENDING_128/PRE/GAMMA/RSLC/custom",
            ):
                with self.subTest(output=output), self.assertRaisesRegex(
                    ValueError, "canonical case path"
                ):
                    layout.require_product(output, "RSLC", "output")

    def test_requires_inputs_in_project_and_product_members_in_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            layout = PreprocessingCaseLayout.bind(project, "SAOCOM/GUIZHOU")

            images = project / "SAOCOM/GUIZHOU/PRE/GAMMA/SLC/images_saocom"
            self.assertEqual(
                layout.require_product_member(images, "SLC", "images_file"), images
            )
            with self.assertRaisesRegex(ValueError, "canonical case path"):
                layout.require_product_member(
                    project / "lists/images_saocom", "SLC", "images_file"
                )
            with self.assertRaisesRegex(ValueError, "outside project root"):
                layout.require_project_input(root / "outside", "raw_dir")

    def test_resolves_symlinks_before_accepting_case_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            outside = root / "outside"
            project.mkdir()
            outside.mkdir()
            (project / "S1").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "outside project root"):
                PreprocessingCaseLayout.bind(project, "S1/ASCENDING_128")

    def test_log_root_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            case = project / "S1/ASCENDING_128"
            outside = root / "outside"
            case.mkdir(parents=True)
            outside.mkdir()
            (case / "LOG").symlink_to(outside, target_is_directory=True)
            layout = PreprocessingCaseLayout.bind(project, "S1/ASCENDING_128")

            with self.assertRaisesRegex(ValueError, "outside case root"):
                _ = layout.log_root


if __name__ == "__main__":
    unittest.main()
