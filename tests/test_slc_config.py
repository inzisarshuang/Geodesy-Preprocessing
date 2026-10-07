"""Tests for project-local Sentinel-1 SLC configuration."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.sar.slc_generation.gamma.tops.config import (
    load_slc_config,
    select_slc_case,
    slc_case_arguments,
)
from geodesy_preprocessing.sar.slc_generation.gamma.tops.config_cli import main


class SlcConfigTests(unittest.TestCase):
    """Resolve project paths, defaults, and explicit case selection."""

    def _project(self, root: Path, *, two_cases: bool = True) -> Path:
        project = root / "study"
        config_dir = project / "config" / "preprocessing"
        config_dir.mkdir(parents=True)
        sections: list[str] = []
        for name, year, orbit in (
            ("2024_154", "2024", "154"),
            ("2026_001", "2026", "001"),
        )[: 2 if two_cases else 1]:
            base = project / f"RAW_{year}"
            raw = base / f"Orbit_{orbit}"
            raw.mkdir(parents=True)
            (raw / f"S1A_{year}0905_{orbit}.SAFE").mkdir()
            (base / f"images_{orbit}").write_text(f"{year}0905\n", encoding="ascii")
            work = base / f"S1_WORK_{orbit}"
            work.mkdir()
            (work / f"ref_burst_number_table_{orbit}").write_text(
                "iw1_number_of_bursts: 1\n", encoding="ascii"
            )
            sections.append(
                "\n".join(
                    (
                        f"[case:{name}]",
                        f"raw_dir = RAW_{year}/Orbit_{orbit}",
                        f"slc_dir = RAW_{year}/SLC_{orbit}",
                        f"images = RAW_{year}/images_{orbit}",
                        "burst_table = "
                        f"RAW_{year}/S1_WORK_{orbit}/ref_burst_number_table_{orbit}",
                    )
                )
            )
        config = config_dir / "gamma_sar_tops_slc.cfg"
        config.write_text(
            "\n".join(
                (
                    "[project]",
                    "root = ../..",
                    "",
                    "[defaults]",
                    "opod_dir = -",
                    "range_looks = 7",
                    "azimuth_looks = 2",
                    "polarization = -",
                    "workflow = safe",
                    "",
                    *sections,
                    "",
                )
            ),
            encoding="utf-8",
        )
        return config

    def test_loads_ordered_cases_and_resolves_paths_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))

            cases = load_slc_config(config)

            self.assertEqual([case.name for case in cases], ["2024_154", "2026_001"])
            first = cases[0]
            project = config.parents[2]
            self.assertEqual(first.raw_dir, project / "RAW_2024" / "Orbit_154")
            self.assertEqual(first.slc_dir, project / "RAW_2024" / "SLC_154")
            self.assertIsNone(first.opod_dir)
            self.assertEqual(first.range_looks, 7)
            self.assertEqual(first.azimuth_looks, 2)
            self.assertEqual(first.polarization, "-")
            self.assertEqual(first.workflow_flag, 1)
            self.assertEqual(
                slc_case_arguments(first),
                (
                    str(project / "RAW_2024" / "Orbit_154"),
                    str(project / "RAW_2024" / "SLC_154"),
                    "-",
                    str(project / "RAW_2024" / "images_154"),
                    str(
                        project
                        / "RAW_2024"
                        / "S1_WORK_154"
                        / "ref_burst_number_table_154"
                    ),
                    "7",
                    "2",
                    "-",
                    "1",
                ),
            )

    def test_multiple_cases_require_selection_and_unknown_case_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cases = load_slc_config(self._project(Path(directory)))

            with self.assertRaisesRegex(ValueError, "--case is required"):
                select_slc_case(cases, None)
            with self.assertRaisesRegex(ValueError, "unknown SLC case"):
                select_slc_case(cases, "missing")
            self.assertEqual(select_slc_case(cases, "2026_001").name, "2026_001")

    def test_rejects_unknown_keys_and_invalid_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory), two_cases=False)
            text = config.read_text(encoding="utf-8")
            config.write_text(text.replace("workflow = safe", "workflow = other"))
            with self.assertRaisesRegex(ValueError, "workflow"):
                load_slc_config(config)

            config.write_text(text.replace("range_looks = 7", "mystery = 7"))
            with self.assertRaisesRegex(ValueError, "unknown key"):
                load_slc_config(config)

    def test_cli_runs_one_selected_case_through_existing_slc_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory), two_cases=False)
            project = config.parents[2]
            destination = project / "RAW_2024" / "SLC_154" / "20240905"
            destination.mkdir(parents=True)
            (destination / "20240905.vv.mli").write_bytes(b"existing")
            terminal = io.StringIO()
            environment = {
                "GEODESY_PROGRESS_PROTOCOL": "1",
                "GEODESY_TASK": "PRE_S1_SLC",
            }

            with patch.dict(os.environ, environment), redirect_stdout(terminal):
                exit_code = main(["--config", str(config), "--case", "2024_154"])

            self.assertEqual(exit_code, 0)
            self.assertIn('"status":"skipped"', terminal.getvalue())
            self.assertIn('"task":"PRE_S1_SLC"', terminal.getvalue())

    def test_cli_reports_configuration_errors_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))
            error = io.StringIO()

            with redirect_stderr(error):
                exit_code = main(["--config", str(config)])

            self.assertEqual(exit_code, 2)
            self.assertIn("--case is required", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())


if __name__ == "__main__":
    unittest.main()
