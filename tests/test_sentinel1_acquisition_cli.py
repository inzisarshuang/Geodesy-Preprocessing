"""Tests for the installed Sentinel-1 query/download/merge command."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.acquisition.sentinel1 import cli
from geodesy_preprocessing.sar.acquisition.sentinel1.manifest import Sentinel1QueryManifest
from geodesy_preprocessing.sar.acquisition.sentinel1.models import (
    Sentinel1AcquisitionCase,
    Sentinel1ProductType,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.query import Sentinel1QueryResult


class Sentinel1AcquisitionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = root / "s1.cfg"
        self.config.write_text("placeholder")
        self.case = Sentinel1AcquisitionCase(
            name="main", project_root=root, kml_file=root / "a.kml", bbox=None,
            start_date="2026-09-08", end_date="2026-09-09",
            product_type=Sentinel1ProductType.BURST, beam_mode="IW", polarizations=("VV",),
            provider="asf", tracks=(154,), orbit_direction="DESCENDING", coverage_threshold=.99,
            query_dir=root / "QUERY", download_dir=root / "INPUT", download_threads=2,
            max_retries=3, retry_delay_seconds=0, keep_burst_files=True,
        )
        manifest = Sentinel1QueryManifest(1, "main", "a" * 64, "b" * 64, (), ())
        self.query = Sentinel1QueryResult(self.case.query_dir, (), (), None, manifest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _base(self):
        return (
            mock.patch.object(cli, "load_sentinel1_acquisition_config", return_value=(self.case,)),
            mock.patch.object(cli, "select_sentinel1_acquisition_case", return_value=self.case),
        )

    def test_actions_are_required_and_mutually_exclusive(self) -> None:
        for arguments in (
            ["--config", str(self.config)],
            ["--config", str(self.config), "--query", "--download"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as raised:
                cli.main(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_query_uses_named_case_without_touching_credentials(self) -> None:
        result = replace(self.query, all_products=(), selected_products=())
        first, second = self._base()
        output = io.StringIO()
        with first, second, mock.patch.object(cli, "publish_sentinel1_query", return_value=result), mock.patch.object(
            cli, "earthdata_session_from_environment", side_effect=AssertionError("credentials touched")
        ), redirect_stdout(output):
            code = cli.main(["--config", str(self.config), "--case", "main", "--query"])
        self.assertEqual(code, 0)
        self.assertIn("query complete", output.getvalue())

    def test_download_and_merge_resolve_credentials_and_dispatch(self) -> None:
        for action, target in (("--download", "download_sentinel1"), ("--merge", "merge_sentinel1_bursts")):
            first, second = self._base()
            with self.subTest(action=action), first, second, mock.patch.object(
                cli, "load_query_for_download", return_value=self.query
            ), mock.patch.object(cli, "earthdata_session_from_environment", return_value="SESSION") as auth, mock.patch.object(
                cli, target
            ) as run:
                self.assertEqual(cli.main(["--config", str(self.config), action]), 0)
            auth.assert_called_once()
            run.assert_called_once()

    def test_non_burst_merge_is_an_input_error_before_credentials(self) -> None:
        self.case = replace(self.case, product_type=Sentinel1ProductType.SLC)
        first, second = self._base()
        with first, second, mock.patch.object(cli, "load_query_for_download", return_value=self.query), mock.patch.object(
            cli, "earthdata_session_from_environment", side_effect=AssertionError("credentials touched")
        ), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--config", str(self.config), "--merge"]), 2)

    def test_workflow_progress_exit_codes_and_secret_redaction(self) -> None:
        first, second = self._base()
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"GEODESY_PROGRESS_PROTOCOL": "1", "EARTHDATA_TOKEN": "TOKEN-SECRET"}, clear=False), first, second, mock.patch.object(
            cli, "load_query_for_download", return_value=self.query
        ), mock.patch.object(cli, "earthdata_session_from_environment", return_value=object()), mock.patch.object(
            cli, "download_sentinel1"
        ) as run, redirect_stdout(output):
            run.side_effect = lambda case, query, session, progress_sink: progress_sink("[1/1] scene: complete")
            self.assertEqual(cli.main(["--config", str(self.config), "--download"]), 0)
        self.assertIn("@@GEODESY_PROGRESS", output.getvalue())
        self.assertIn("PRE_COMMON_SENTINEL1_ACQUISITION", output.getvalue())

        for error, expected in ((RuntimeError("bad TOKEN-SECRET"), 1), (KeyboardInterrupt(), 130)):
            first, second = self._base()
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {"EARTHDATA_TOKEN": "TOKEN-SECRET"}, clear=False), first, second, mock.patch.object(
                cli, "publish_sentinel1_query", side_effect=error
            ), redirect_stderr(stderr):
                self.assertEqual(cli.main(["--config", str(self.config), "--query"]), expected)
            self.assertNotIn("TOKEN-SECRET", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
