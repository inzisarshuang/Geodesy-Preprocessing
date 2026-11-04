"""Tests for centralized PRE quicklook publication."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.common.figures import (
    centralize_pre_figures,
    pre_figures_complete,
    repair_pre_figures,
)


class PreprocessingFigureTests(unittest.TestCase):
    def test_images_move_to_deterministic_central_tree_without_science_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            method = Path(directory) / "S1/ASCENDING_128/GAMMA"
            science = method / "SLC/20240101"
            science.mkdir(parents=True)
            (science / "20240101.vv.mli").write_bytes(b"science")
            image = science / "20240101.vv.mli.bmp"
            image.write_bytes(b"image")
            figures = method / "FIGURES/PRE/SLC"

            published = centralize_pre_figures(method / "SLC", figures)

            expected = figures / "20240101/20240101.vv.mli.bmp"
            self.assertEqual(published, (expected,))
            self.assertFalse(image.exists())
            self.assertTrue((science / "20240101.vv.mli").is_file())
            self.assertTrue(pre_figures_complete(published))

    def test_figure_directory_must_not_overlap_science(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            science = Path(directory) / "RSLC"
            science.mkdir()
            with self.assertRaisesRegex(ValueError, "must not be inside"):
                centralize_pre_figures(science, science / "quicklooks")

    def test_missing_figure_can_be_repaired_without_touching_science(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            science = root / "RSLC/scene.rslc"
            science.parent.mkdir()
            science.write_bytes(b"science")
            before = science.read_bytes()
            manifest = {
                "figures": [
                    {"path": "RSLC/scene.bmp", "command": ["rasSLC", "scene.rslc"]}
                ]
            }
            calls: list[tuple[str, ...]] = []

            def render(command: tuple[str, ...], destination: Path) -> None:
                calls.append(command)
                destination.write_bytes(b"figure")

            repaired = repair_pre_figures(manifest, root / "FIGURES/PRE", render)

            self.assertEqual(repaired, (root / "FIGURES/PRE/RSLC/scene.bmp",))
            self.assertEqual(calls, [("rasSLC", "scene.rslc")])
            self.assertEqual(science.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
