"""Tests for project-level, dataset-first topography paths."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.dem.layout import TopographyDatasetLayout


class TopographyDatasetLayoutTests(unittest.TestCase):
    def test_builds_dataset_first_source_and_gamma_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()

            layout = TopographyDatasetLayout.bind(project, "jishishan_cop30")

            root = project / "TOPOGRAPHY/jishishan_cop30"
            self.assertEqual(layout.dataset_id, "jishishan_cop30")
            self.assertEqual(layout.topography_root, project / "TOPOGRAPHY")
            self.assertEqual(layout.root, root)
            self.assertEqual(layout.source_dir, root / "SOURCE")
            self.assertEqual(layout.source_raster, root / "SOURCE/jishishan_cop30.tif")
            self.assertEqual(
                layout.download_manifest, root / "SOURCE/download_manifest.json"
            )
            self.assertEqual(layout.gamma_dir, root / "GAMMA")
            self.assertEqual(layout.gamma_dem, root / "GAMMA/jishishan_cop30.dem")
            self.assertEqual(
                layout.gamma_par, root / "GAMMA/jishishan_cop30.dem.par"
            )
            self.assertEqual(
                layout.conversion_manifest, root / "GAMMA/conversion_manifest.json"
            )
            self.assertEqual(
                layout.topography_manifest, root / "topography_manifest.json"
            )
            self.assertEqual(layout.catalog, project / "TOPOGRAPHY/catalog.json")

    def test_rejects_unsafe_dataset_ids_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            outside = workspace / "outside"
            project.mkdir()
            outside.mkdir()

            for dataset_id in ("", ".", "..", "nested/cop30", "/tmp/cop30", "bad id"):
                with self.subTest(dataset_id=dataset_id), self.assertRaises(ValueError):
                    TopographyDatasetLayout.bind(project, dataset_id)

            (project / "TOPOGRAPHY").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "outside project root"):
                TopographyDatasetLayout.bind(project, "jishishan_cop30")


if __name__ == "__main__":
    unittest.main()
