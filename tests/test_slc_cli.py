"""Behavior tests for the compatible Sentinel-1 S1-03 package CLI."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.sar.slc_generation.gamma.tops.cli import main


class TtyBuffer(io.StringIO):
    """String buffer reporting interactive terminal capability."""

    def isatty(self) -> bool:
        return True


class SlcCliTests(unittest.TestCase):
    """Preserve historical positional parsing and progress display modes."""

    def _arguments(self, root: Path, *, workflow: int) -> list[str]:
        raw = root / "RAW"
        raw.mkdir()
        slc = root / "SLC"
        item_dir = slc / "20240905"
        item_dir.mkdir(parents=True)
        (item_dir / "20240905.vv.mli").write_bytes(b"existing")
        images = root / "images"
        images.write_text("20240905\n", encoding="ascii")
        burst = root / "ref_burst_number_table"
        burst.write_text("iw1_number_of_bursts: 1\n", encoding="ascii")
        source = raw / ("S1A_20240905.SAFE" if workflow else "S1A_20240905.zip")
        source.mkdir() if workflow else source.write_bytes(b"zip")
        return [str(raw), str(slc), "-", str(images), str(burst), "4", "1"]

    def test_seven_arguments_keep_zip_default_and_print_plain_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = self._arguments(Path(directory), workflow=0)
            terminal = io.StringIO()
            with redirect_stdout(terminal):
                exit_code = main(arguments)

            self.assertEqual(exit_code, 0)
            self.assertIn("[PRE_GAMMA_SAR_TOPS_SLC] [1/1 100.0%]", terminal.getvalue())
            self.assertIn("status=skipped", terminal.getvalue())

    def test_eighth_zero_or_one_is_workflow_flag_and_ninth_overrides_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = self._arguments(Path(directory), workflow=1)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([*arguments, "1"]), 0)

        with tempfile.TemporaryDirectory() as directory:
            arguments = self._arguments(Path(directory), workflow=1)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([*arguments, "-", "1"]), 0)

    def test_recorded_mode_emits_raw_protocol_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = self._arguments(Path(directory), workflow=1)
            terminal = io.StringIO()
            with patch.dict(os.environ, {"GEODESY_PROGRESS_PROTOCOL": "1"}), redirect_stdout(terminal):
                self.assertEqual(main([*arguments, "1"]), 0)

            lines = terminal.getvalue().splitlines()
            self.assertTrue(lines[0].startswith("@@GEODESY_PROGRESS "))
            self.assertIn('"status":"skipped"', lines[-1])

    def test_recorded_mode_uses_workflow_injected_task_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = self._arguments(Path(directory), workflow=1)
            terminal = io.StringIO()
            environment = {
                "GEODESY_PROGRESS_PROTOCOL": "1",
                "GEODESY_TASK": "PRE_GAMMA_SAR_TOPS_SLC_SHADOW",
            }
            with patch.dict(os.environ, environment), redirect_stdout(terminal):
                self.assertEqual(main([*arguments, "1"]), 0)

            self.assertIn('"task":"PRE_GAMMA_SAR_TOPS_SLC_SHADOW"', terminal.getvalue())

    def test_direct_tty_uses_twenty_cell_progress_bar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = self._arguments(Path(directory), workflow=1)
            terminal = TtyBuffer()
            with redirect_stdout(terminal):
                self.assertEqual(main([*arguments, "1"]), 0)

            self.assertIn("[████████████████████]", terminal.getvalue())
            self.assertIn("1/1  100.0%", terminal.getvalue())

    def test_invalid_workflow_returns_nonzero_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = self._arguments(Path(directory), workflow=1)
            error = io.StringIO()
            with redirect_stderr(error), redirect_stdout(io.StringIO()):
                exit_code = main([*arguments, "-", "2"])

            self.assertNotEqual(exit_code, 0)
            self.assertIn("workflow_flag", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())


if __name__ == "__main__":
    unittest.main()
