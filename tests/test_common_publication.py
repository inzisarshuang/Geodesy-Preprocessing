"""Tests for shared transactional preprocessing publication."""

from __future__ import annotations

import tempfile
import unittest
from os import replace
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.common.publication import atomic_replace_directory


class AtomicReplaceDirectoryTests(unittest.TestCase):
    """Exercise replace-on-success behavior used by processing workflows."""

    def test_existing_output_is_replaced_by_staging(self) -> None:
        """A formal rerun publishes new products instead of skipping old ones."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "result"
            staging = root / ".result.stage"
            output.mkdir()
            staging.mkdir()
            (output / "old.txt").write_text("old", encoding="utf-8")
            (staging / "new.txt").write_text("new", encoding="utf-8")

            atomic_replace_directory(staging, output)

            self.assertFalse(staging.exists())
            self.assertFalse((output / "old.txt").exists())
            self.assertEqual((output / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertEqual(tuple(root.glob("*.backup")), ())

    def test_failed_publish_and_rollback_preserve_backup(self) -> None:
        """A rollback error leaves the previous result available for recovery."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "result"
            staging = root / ".result.stage"
            output.mkdir()
            staging.mkdir()
            (output / "old.txt").write_text("old", encoding="utf-8")

            def fail_publishing(src: Path, dst: Path) -> None:
                if dst == output:
                    raise OSError("simulated rename failure")
                replace(src, dst)

            with patch(
                "geodesy_preprocessing.common.publication.os.replace",
                side_effect=fail_publishing,
            ):
                with self.assertRaisesRegex(RuntimeError, r"\.backup/previous") as error:
                    atomic_replace_directory(staging, output)

            (saved,) = root.glob("*.backup/previous")
            self.assertIn(str(saved), str(error.exception))
            self.assertEqual((saved / "old.txt").read_text(encoding="utf-8"), "old")

    def test_output_symlink_keeps_the_referenced_result(self) -> None:
        """Reject a symlink target before any output or staging rename."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original"
            output = root / "result"
            staging = root / ".result.stage"
            original.mkdir()
            staging.mkdir()
            output.symlink_to(original, target_is_directory=True)
            (original / "old.txt").write_text("old", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "symlink"):
                atomic_replace_directory(staging, output)

            self.assertTrue(output.is_symlink())
            self.assertTrue(staging.is_dir())
            self.assertEqual((original / "old.txt").read_text(encoding="utf-8"), "old")


if __name__ == "__main__":
    unittest.main()
