"""Behavior tests for the installed-style Other-SAR SLC command."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fixtures.other_sar import write_cosmo_product
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.cli import main


FIXTURE = Path(__file__).parent / "fixtures/fake_other_sar_command.py"
SOURCE = Path(__file__).parents[1] / "src"


class OtherSarSlcCliTests(unittest.TestCase):
    """Exercise help, dry-run, case selection, progress, and exit status."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "RAW/main").mkdir(parents=True)
        write_cosmo_product(self.root / "RAW/main", sensor="csg", date="20260922")
        self.config = self.root / "gamma_sar_slc.cfg"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_config(self, *, multiple: bool = False) -> None:
        extra = """
[case:second]
sensor = csg
raw_dir = RAW/main
slc_dir = SLC/second
import_command = {python} {fixture} cosmo
""" if multiple else ""
        self.config.write_text(
            f"""[project]
root = {self.root}
[defaults]
range_looks = 10
azimuth_looks = 10
[case:main]
sensor = csg
raw_dir = RAW/main
slc_dir = SLC/main
import_command = {sys.executable} {FIXTURE} cosmo
{extra.format(python=sys.executable, fixture=FIXTURE)}""",
            encoding="utf-8",
        )

    def test_help_and_dry_run_show_human_operational_information(self) -> None:
        terminal = io.StringIO()
        with self.assertRaises(SystemExit) as stopped, redirect_stdout(terminal):
            main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--config", terminal.getvalue())
        self._write_config()
        terminal = io.StringIO()
        with redirect_stdout(terminal):
            self.assertEqual(main(["--config", str(self.config), "--dry-run"]), 0)
        self.assertIn("sensor=csg", terminal.getvalue())
        self.assertIn("20260922", terminal.getvalue())
        self.assertFalse((self.root / "SLC").exists())

    def test_named_case_runs_and_invalid_case_returns_two(self) -> None:
        self._write_config(multiple=True)
        terminal = io.StringIO()
        with redirect_stdout(terminal):
            self.assertEqual(
                main(["--config", str(self.config), "--case", "main"]), 0
            )
        self.assertIn("[PRE_GAMMA_SAR_SLC] [1/1 100.0%]", terminal.getvalue())
        self.assertTrue((self.root / "SLC/main/HH_SLC/CSG/20260922.slc").is_file())

        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(
                main(["--config", str(self.config), "--case", "missing"]), 2
            )
        self.assertIn("unknown Other-SAR case", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_external_failure_returns_one_and_recorded_mode_emits_protocol(self) -> None:
        self._write_config()
        error = io.StringIO()
        with patch.dict(os.environ, {"FAKE_OTHER_SAR_FAIL_ITEM": "20260922"}), redirect_stderr(error), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.config)]), 1)
        self.assertIn("forced failure", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

        with patch.dict(os.environ, {"GEODESY_PROGRESS_PROTOCOL": "1"}), redirect_stdout(terminal := io.StringIO()):
            self.assertEqual(main(["--config", str(self.config)]), 0)
        self.assertTrue(terminal.getvalue().startswith("@@GEODESY_PROGRESS "))

    def test_module_dry_run_works_outside_checkout(self) -> None:
        self._write_config()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.cli",
                "--config",
                str(self.config),
                "--dry-run",
            ],
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("20260922", completed.stdout)


if __name__ == "__main__":
    unittest.main()
