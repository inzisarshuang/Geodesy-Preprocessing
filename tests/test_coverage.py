"""Behavior tests for optional Sentinel-1 coverage rendering."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from geodesy_preprocessing.sar.acquisition.sentinel1.burst_table import load_roi
from geodesy_preprocessing.sar.acquisition.sentinel1.coverage import save_coverage


@unittest.skipUnless(importlib.util.find_spec("matplotlib"), "plot extra not installed")
class CoverageTests(unittest.TestCase):
    """Catch invalid JPEG publication, leaked figures, and partial replacement."""

    @staticmethod
    def _inputs(root: Path) -> tuple[dict[int, Path], object]:
        iw = root / "IW_1.txt"
        np.savetxt(iw, [[1, 0, 0, 0, 1, 1, 1, 1, 0]])
        roi_path = root / "roi.txt"
        roi_path.write_text(
            "0.25 0.25\n0.75 0.25\n0.75 0.75\n0.25 0.75\n0.25 0.25\n",
            encoding="ascii",
        )
        return {1: iw}, load_roi(roi_path)

    def test_save_coverage_publishes_a_jpeg_and_closes_figure(self) -> None:
        from matplotlib import pyplot as plt

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            iw_files, roi = self._inputs(root)
            destination = root / "Coverage_Python.jpg"

            result = save_coverage(iw_files, roi, destination)

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes()[:2], b"\xff\xd8")
            self.assertEqual(plt.get_fignums(), [])

    def test_failed_publish_preserves_existing_jpeg_and_closes_figure(self) -> None:
        from matplotlib import pyplot as plt

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            iw_files, roi = self._inputs(root)
            destination = root / "Coverage_Python.jpg"
            destination.write_bytes(b"old jpeg")

            with patch(
                "geodesy_preprocessing.sar.acquisition.sentinel1.coverage.os.replace",
                side_effect=OSError("injected coverage failure"),
            ), self.assertRaisesRegex(OSError, "coverage failure"):
                save_coverage(iw_files, roi, destination)

            self.assertEqual(destination.read_bytes(), b"old jpeg")
            self.assertEqual(plt.get_fignums(), [])
            self.assertEqual(
                [path for path in root.iterdir() if path != destination and path != iw_files[1] and path.name != "roi.txt"],
                [],
            )


if __name__ == "__main__":
    unittest.main()
