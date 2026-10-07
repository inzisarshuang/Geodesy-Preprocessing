"""Command behavior tests for the Sentinel-1 burst-table CLI."""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from geodesy_preprocessing.sar.acquisition.sentinel1.burst_table_cli import main


class BurstTableCLITests(unittest.TestCase):
    """Catch command-contract, lazy-dependency, and transaction regressions."""

    @staticmethod
    def _create_workspace(root: Path, suffix: str = "") -> Path:
        suffix_part = f"_{suffix}" if suffix else ""
        burst_rows = [
            [1, 0, 0, 0, 1, 1, 1, 1, 0],
            [2, 0, 1, 0, 2, 1, 2, 1, 1],
        ]
        for swath in (1, 2, 3):
            np.savetxt(root / f"IW_{swath}{suffix_part}.txt", burst_rows)
        (root / f"Old_burst_number_table{suffix_part}").write_text(
            "zipfile: source.zip\n"
            "iw1_number_of_bursts: 2\n"
            "iw1_first_burst: 100.5\n"
            "iw1_last_burst: 101.5\n"
            "iw2_number_of_bursts: 2\n"
            "iw2_first_burst: 200.5\n"
            "iw2_last_burst: 201.5\n"
            "iw3_number_of_bursts: 2\n"
            "iw3_first_burst: 300.5\n"
            "iw3_last_burst: 301.5\n",
            encoding="ascii",
        )
        roi = root / "roi.txt"
        roi.write_text(
            "0.25 0.25\n0.75 0.25\n0.75 0.75\n0.25 0.75\n0.25 0.25\n",
            encoding="ascii",
        )
        return roi

    def test_suffix_outputs_and_complete_swath_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            roi = self._create_workspace(root, "060")

            with contextlib.chdir(root):
                exit_code = main([str(roi), "1", "060"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                (root / "burst_itab_060").read_bytes(),
                b"1 1 1\n2 1 1\n3 1 1\n",
            )
            self.assertEqual(
                (root / "ref_burst_number_table_060").read_bytes(),
                b"zipfile:    ref.zip\n"
                b"iw1_number_of_bursts: 1\n"
                b"iw1_first_burst:      100.500000\n"
                b"iw1_last_burst:       100.500000\n"
                b"iw2_number_of_bursts: 1\n"
                b"iw2_first_burst:      200.500000\n"
                b"iw2_last_burst:       200.500000\n"
                b"iw3_number_of_bursts: 1\n"
                b"iw3_first_burst:      300.500000\n"
                b"iw3_last_burst:       300.500000\n",
            )

    def test_dash_roi_selects_all_bursts_and_prints_notice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._create_workspace(root)
            output = io.StringIO()

            with contextlib.chdir(root), contextlib.redirect_stdout(output):
                exit_code = main(["-", "1"])

            self.assertEqual(exit_code, 0)
            self.assertIn("all burst areas", output.getvalue())
            self.assertEqual(
                (root / "burst_itab").read_bytes(),
                b"1 1 2\n2 1 2\n3 1 2\n",
            )

    def test_image_flag_one_never_imports_matplotlib(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            roi = self._create_workspace(root)
            original_import = builtins.__import__

            def guarded_import(name: str, *args: object, **kwargs: object) -> object:
                if name == "matplotlib" or name.startswith("matplotlib."):
                    raise AssertionError("Matplotlib imported for image_FLAG=1")
                return original_import(name, *args, **kwargs)

            with contextlib.chdir(root), patch(
                "builtins.__import__", side_effect=guarded_import
            ):
                self.assertEqual(main([str(roi), "1"]), 0)

    def test_invalid_image_flag_has_nonzero_parser_exit(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(["roi.txt", "2"])
        self.assertNotEqual(raised.exception.code, 0)
        self.assertIn("invalid choice", error.getvalue())

    def test_no_overlap_preserves_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            roi = self._create_workspace(root)
            roi.write_text(
                "10 10\n11 10\n11 11\n10 11\n10 10\n", encoding="ascii"
            )
            (root / "burst_itab").write_bytes(b"old itab\n")
            (root / "ref_burst_number_table").write_bytes(b"old reference\n")

            with contextlib.chdir(root), self.assertRaisesRegex(
                ValueError, "no bursts overlap"
            ):
                main([str(roi), "1"])

            self.assertEqual((root / "burst_itab").read_bytes(), b"old itab\n")
            self.assertEqual(
                (root / "ref_burst_number_table").read_bytes(), b"old reference\n"
            )

    def test_second_publish_failure_restores_both_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            roi = self._create_workspace(root)
            itab = root / "burst_itab"
            reference = root / "ref_burst_number_table"
            itab.write_bytes(b"old itab\n")
            reference.write_bytes(b"old reference\n")
            real_replace = os.replace
            call_count = 0

            def fail_fourth_replace(source: object, destination: object) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 4:
                    raise OSError("injected second publish failure")
                real_replace(source, destination)

            with contextlib.chdir(root), patch(
                "geodesy_preprocessing.sar.acquisition.sentinel1."
                "burst_table_cli._replace",
                side_effect=fail_fourth_replace,
            ), self.assertRaisesRegex(OSError, "second publish"):
                main([str(roi), "1"])

            self.assertEqual(itab.read_bytes(), b"old itab\n")
            self.assertEqual(reference.read_bytes(), b"old reference\n")
            self.assertEqual(
                sorted(path.name for path in root.iterdir() if path.name.startswith(".")),
                [],
            )

    def test_staging_failure_removes_an_already_written_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            roi = self._create_workspace(root)

            with contextlib.chdir(root), patch(
                "geodesy_preprocessing.sar.acquisition.sentinel1."
                "burst_table_cli.write_reference_burst_table",
                side_effect=OSError("injected staging failure"),
            ), self.assertRaisesRegex(OSError, "staging failure"):
                main([str(roi), "1"])

            self.assertEqual(
                sorted(path.name for path in root.iterdir() if path.name.startswith(".")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
