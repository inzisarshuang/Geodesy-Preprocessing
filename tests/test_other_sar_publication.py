"""Behavior tests for grouped Other-SAR output publication."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.models import ProductOutput
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.publication import (
    classify_output_group,
    publish_images_table,
    publish_output_group,
)


class OtherSarPublicationTests(unittest.TestCase):
    """Ensure a scene becomes visible completely or not at all."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.stage = self.root / ".scene.stage"
        self.stage.mkdir()
        self.dest = self.root / "SLC"
        self.outputs = (
            ProductOutput("HH", Path("scene.slc"), self.dest / "20260922.slc"),
            ProductOutput(
                "HH", Path("scene.slc.par"), self.dest / "20260922.slc.par"
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_staged_outputs(self) -> None:
        (self.stage / "scene.slc").write_bytes(b"slc")
        (self.stage / "scene.slc.par").write_text("range_samples: 2\n")

    def test_complete_group_skips_and_partial_group_is_rejected(self) -> None:
        self.assertEqual(classify_output_group(self.outputs), "absent")
        self.dest.mkdir()
        for output in self.outputs:
            output.destination.write_bytes(b"complete")
        self.assertEqual(classify_output_group(self.outputs), "complete")
        self.outputs[-1].destination.unlink()
        with self.assertRaisesRegex(ValueError, "incomplete output group"):
            classify_output_group(self.outputs)

    def test_publication_moves_every_valid_file_into_place(self) -> None:
        self._make_staged_outputs()
        publish_output_group(self.stage, self.outputs)
        self.assertEqual(self.outputs[0].destination.read_bytes(), b"slc")
        self.assertEqual(
            self.outputs[1].destination.read_text(), "range_samples: 2\n"
        )
        self.assertFalse((self.stage / "scene.slc").exists())

    def test_empty_staged_file_and_destination_symlink_are_rejected(self) -> None:
        self._make_staged_outputs()
        (self.stage / "scene.slc").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            publish_output_group(self.stage, self.outputs)
        (self.stage / "scene.slc").write_bytes(b"slc")
        self.dest.mkdir()
        target = self.root / "elsewhere"
        target.write_bytes(b"safe")
        self.outputs[0].destination.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            publish_output_group(self.stage, self.outputs)

    def test_staged_file_and_images_table_symlinks_are_rejected(self) -> None:
        self._make_staged_outputs()
        real_staged = self.stage / "real-scene.slc"
        real_staged.write_bytes(b"slc")
        (self.stage / "scene.slc").unlink()
        (self.stage / "scene.slc").symlink_to(real_staged)
        with self.assertRaisesRegex(ValueError, "symlink"):
            publish_output_group(self.stage, self.outputs)

        images_target = self.root / "existing-images"
        images_target.write_text("do not replace\n", encoding="utf-8")
        images_link = self.root / "images"
        images_link.symlink_to(images_target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            publish_images_table(images_link, ("20260922",))
        self.assertEqual(images_target.read_text(encoding="utf-8"), "do not replace\n")

    def test_second_rename_failure_rolls_back_the_first_output(self) -> None:
        self._make_staged_outputs()
        from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid import (
            publication,
        )

        real_replace = publication._replace_path
        calls = 0

        def fail_second(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("forced second rename failure")
            real_replace(source, destination)

        with patch.object(publication, "_replace_path", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "forced second"):
                publish_output_group(self.stage, self.outputs)

        self.assertFalse(self.outputs[0].destination.exists())
        self.assertFalse(self.outputs[1].destination.exists())
        self.assertEqual((self.stage / "scene.slc").read_bytes(), b"slc")
        self.assertTrue((self.stage / "scene.slc.par").is_file())

    def test_images_table_is_sorted_unique_and_replacement_is_atomic(self) -> None:
        images = self.root / "lists/images_alos2"
        publish_images_table(images, ("20260923", "20260922", "20260922"))
        self.assertEqual(images.read_text(), "20260922\n20260923\n")
        before = images.read_bytes()
        with patch(
            "geodesy_preprocessing.sar.slc_generation.gamma.radar_grid."
            "publication.os.replace",
            side_effect=OSError("forced replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "forced replace"):
                publish_images_table(images, ("20260924",))
        self.assertEqual(images.read_bytes(), before)
        self.assertEqual(list(images.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
