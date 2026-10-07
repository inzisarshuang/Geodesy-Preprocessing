"""Tests for the installed NISAR query/download command."""

from __future__ import annotations

import io
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.acquisition.nisar import cli
from geodesy_preprocessing.sar.acquisition.nisar.manifest import NisarQueryManifest
from geodesy_preprocessing.sar.acquisition.nisar.models import (
    NisarFrequencyMode,
    NisarProductType,
    NisarSearchCase,
)
from geodesy_preprocessing.sar.acquisition.nisar.query import NisarQueryResult


class NisarAcquisitionCliTests(unittest.TestCase):
    """Keep query/download separation, exit codes, and Workflow events stable."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = root / "nisar.cfg"
        self.config.write_text("placeholder", encoding="utf-8")
        self.case = NisarSearchCase(
            name="main",
            project_root=root,
            kml_file=root / "area.kml",
            start_date="2026-01-01",
            end_date="2026-02-01",
            product_type=NisarProductType.RSLC,
            band="L",
            polarizations=("HH",),
            provider="asf",
            frequency_mode=NisarFrequencyMode.MAIN_ONLY,
            tracks=None,
            frames=None,
            orbit_direction=None,
            query_dir=root / "QUERY",
            download_dir=root / "INPUT",
        )
        self.manifest = NisarQueryManifest(1, "main", "a" * 64, "b" * 64, (), ())
        self.query = NisarQueryResult(self.case.query_dir, (), (), self.manifest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_query_and_download_are_required_and_mutually_exclusive(self) -> None:
        for arguments in (
            ["--config", str(self.config)],
            ["--config", str(self.config), "--query", "--download"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as raised:
                cli.main(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_help_describes_shared_rslc_and_gslc_acquisition(self) -> None:
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
            cli.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("RSLC or GSLC", output.getvalue())

    def test_query_selects_named_case_without_resolving_credentials(self) -> None:
        output = io.StringIO()
        with mock.patch.object(cli, "load_nisar_config", return_value=(self.case,)), mock.patch.object(
            cli, "select_nisar_case", return_value=self.case
        ) as select, mock.patch.object(
            cli, "run_nisar_query", return_value=self.query
        ) as run_query, mock.patch.object(
            cli, "_earthdata_session", side_effect=AssertionError("credentials touched")
        ), redirect_stdout(output):
            code = cli.main(
                ["--config", str(self.config), "--case", "main", "--query"]
            )

        self.assertEqual(code, 0)
        select.assert_called_once_with((self.case,), "main")
        self.assertIs(run_query.call_args.args[0], self.case)
        self.assertIn("query complete", output.getvalue())

    def test_download_requires_query_manifest_before_credentials(self) -> None:
        with mock.patch.object(cli, "load_nisar_config", return_value=(self.case,)), mock.patch.object(
            cli, "select_nisar_case", return_value=self.case
        ), mock.patch.object(
            cli, "_earthdata_session", side_effect=AssertionError("credentials touched")
        ), redirect_stderr(io.StringIO()):
            code = cli.main(["--config", str(self.config), "--download"])

        self.assertEqual(code, 2)

    def test_download_emits_direct_and_workflow_progress(self) -> None:
        self.case.query_dir.mkdir()
        (self.case.query_dir / "query_manifest.json").write_text("{}", encoding="utf-8")
        direct = io.StringIO()
        with mock.patch.object(cli, "load_nisar_config", return_value=(self.case,)), mock.patch.object(
            cli, "select_nisar_case", return_value=self.case
        ), mock.patch.object(cli, "load_query_for_download", return_value=self.query), mock.patch.object(
            cli, "_earthdata_session", return_value=object()
        ), mock.patch.object(cli, "download_nisar") as download, redirect_stdout(direct):
            download.side_effect = lambda case, query, session, progress_sink: progress_sink(
                "[1/1] scene: complete"
            )
            self.assertEqual(cli.main(["--config", str(self.config), "--download"]), 0)
        self.assertIn("[1/1] scene: complete", direct.getvalue())

        workflow = io.StringIO()
        with mock.patch.dict("os.environ", {"GEODESY_PROGRESS_PROTOCOL": "1"}, clear=False), mock.patch.object(
            cli, "load_nisar_config", return_value=(self.case,)
        ), mock.patch.object(cli, "select_nisar_case", return_value=self.case), mock.patch.object(
            cli, "load_query_for_download", return_value=self.query
        ), mock.patch.object(cli, "_earthdata_session", return_value=object()), mock.patch.object(
            cli, "download_nisar"
        ) as download, redirect_stdout(workflow):
            download.side_effect = lambda case, query, session, progress_sink: progress_sink(
                "[1/1] scene: complete"
            )
            self.assertEqual(cli.main(["--config", str(self.config), "--download"]), 0)
        self.assertIn("@@GEODESY_PROGRESS", workflow.getvalue())
        self.assertIn("PRE_COMMON_NISAR_ACQUISITION", workflow.getvalue())
        self.assertIn('"item":"scene"', workflow.getvalue())

    def test_returns_2_for_input_1_for_runtime_and_130_for_interrupt(self) -> None:
        with mock.patch.object(
            cli, "load_nisar_config", side_effect=FileNotFoundError("missing config")
        ), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--config", str(self.config), "--query"]), 2)

        scenarios = ((RuntimeError("provider unavailable"), 1), (KeyboardInterrupt(), 130))
        for error, expected in scenarios:
            with self.subTest(expected=expected), mock.patch.object(
                cli, "load_nisar_config", return_value=(self.case,)
            ), mock.patch.object(cli, "select_nisar_case", return_value=self.case), mock.patch.object(
                cli, "run_nisar_query", side_effect=error
            ), redirect_stderr(io.StringIO()):
                self.assertEqual(
                    cli.main(["--config", str(self.config), "--query"]), expected
                )

    def test_installed_entry_point_targets_nisar_cli(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        self.assertEqual(
            scripts["geodesy-pre-sar-nisar-acquire"],
            "geodesy_preprocessing.sar.acquisition.nisar.cli:main",
        )


if __name__ == "__main__":
    unittest.main()
