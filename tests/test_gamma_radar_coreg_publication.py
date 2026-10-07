"""Tests for GAMMA radar-grid stack destination classification."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.publication import (
    classify_stack_destination,
)


class GammaRadarCoregPublicationTests(unittest.TestCase):
    """Skip only validated complete manifests and reject every partial directory."""

    def test_absent_destination_runs_and_partial_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "RSLC"
            self.assertEqual(classify_stack_destination(destination), "absent")
            destination.mkdir()
            (destination / "some.rslc").write_bytes(b"partial")
            with self.assertRaisesRegex(FileExistsError, "incomplete RSLC destination"):
                classify_stack_destination(destination)


if __name__ == "__main__":
    unittest.main()
