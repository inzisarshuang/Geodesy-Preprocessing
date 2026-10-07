"""Tests for the installed native NISAR GSLC planning and execution command."""

from __future__ import annotations

import io
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.gslc_subsetting.nisar import cli
from geodesy_preprocessing.sar.gslc_subsetting.nisar.runner import GslcRunSummary
from tests.fixtures.nisar_gslc_plan import make_gslc_plan


class NisarGslcCliTests(unittest.TestCase):
    """Keep named selection, plan-only separation, progress, and exit codes stable."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = make_gslc_plan(self.root)
        self.config = self.root / "stack.cfg"
        self.config.write_text("placeholder\n", encoding="utf-8")
        self.case = self.plan.case

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_only_selects_named_case_and_never_runs_stack(self) -> None:
        output = io.StringIO()
        plan_path = self.root / "plan.json"
        with mock.patch.object(cli, "load_nisar_gslc_stack_config", return_value=(self.case,)), mock.patch.object(
            cli, "select_nisar_gslc_stack_case", return_value=self.case
        ) as select, mock.patch.object(
            cli, "build_nisar_gslc_stack_plan", return_value=self.plan
        ), mock.patch.object(
            cli, "publish_gslc_plan", return_value=plan_path
        ) as publish, mock.patch.object(
            cli, "run_nisar_gslc_stack", side_effect=AssertionError("execution started")
        ), redirect_stdout(output):
            code = cli.main(
                ["--config", str(self.config), "--case", "la", "--plan-only"]
            )

        self.assertEqual(code, 0)
        select.assert_called_once_with((self.case,), "la")
        publish.assert_called_once_with(self.plan)
        self.assertIn("plan complete", output.getvalue())

    def test_default_action_executes_and_supports_workflow_progress(self) -> None:
        direct = io.StringIO()
        summary = GslcRunSummary(2, 2, 0, 0, self.case.output_dir)
        with mock.patch.object(cli, "load_nisar_gslc_stack_config", return_value=(self.case,)), mock.patch.object(
            cli, "select_nisar_gslc_stack_case", return_value=self.case
        ), mock.patch.object(
            cli, "build_nisar_gslc_stack_plan", return_value=self.plan
        ), mock.patch.object(cli, "run_nisar_gslc_stack") as run, redirect_stdout(direct):
            def execute(plan, progress_sink):
                progress_sink(
                    '@@GEODESY_PROGRESS {"event":"item_finished","index":1,'
                    '"item":"20260105:HH","status":"complete","task":"PRE_NISAR_GSLC",'
                    '"timestamp":"2026-01-01T00:00:00Z","total":2}'
                )
                return summary
            run.side_effect = execute
            self.assertEqual(cli.main(["--config", str(self.config), "--case", "la"]), 0)
        self.assertIn("[1/2] 20260105:HH: complete", direct.getvalue())
        self.assertIn("stack complete", direct.getvalue())

        workflow = io.StringIO()
        with mock.patch.dict("os.environ", {"GEODESY_PROGRESS_PROTOCOL": "1"}, clear=False), mock.patch.object(
            cli, "load_nisar_gslc_stack_config", return_value=(self.case,)
        ), mock.patch.object(cli, "select_nisar_gslc_stack_case", return_value=self.case), mock.patch.object(
            cli, "build_nisar_gslc_stack_plan", return_value=self.plan
        ), mock.patch.object(cli, "run_nisar_gslc_stack") as run, redirect_stdout(workflow):
            run.side_effect = lambda plan, progress_sink: (
                progress_sink(
                    '@@GEODESY_PROGRESS {"event":"batch_finished","status":"complete",'
                    '"task":"PRE_NISAR_GSLC","timestamp":"2026-01-01T00:00:00Z","total":2}'
                ),
                summary,
            )[1]
            self.assertEqual(cli.main(["--config", str(self.config)]), 0)
        self.assertIn("@@GEODESY_PROGRESS", workflow.getvalue())
        self.assertIn("PRE_NISAR_GSLC", workflow.getvalue())

    def test_returns_2_for_inputs_1_for_runtime_and_130_for_interrupt_with_redaction(self) -> None:
        with mock.patch.object(
            cli, "load_nisar_gslc_stack_config", side_effect=ValueError("bad config")
        ), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--config", str(self.config)]), 2)

        for error, expected in (
            (RuntimeError("failed token=TOKEN-SECRET"), 1),
            (KeyboardInterrupt(), 130),
        ):
            stderr = io.StringIO()
            with self.subTest(expected=expected), mock.patch.object(
                cli, "load_nisar_gslc_stack_config", return_value=(self.case,)
            ), mock.patch.object(cli, "select_nisar_gslc_stack_case", return_value=self.case), mock.patch.object(
                cli, "build_nisar_gslc_stack_plan", return_value=self.plan
            ), mock.patch.object(cli, "run_nisar_gslc_stack", side_effect=error), redirect_stderr(stderr):
                self.assertEqual(cli.main(["--config", str(self.config)]), expected)
            self.assertNotIn("TOKEN-SECRET", stderr.getvalue())

    def test_installed_entry_point_targets_native_gslc_cli(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        self.assertEqual(
            scripts["geodesy-pre-sar-nisar-gslc"],
            "geodesy_preprocessing.sar.gslc_subsetting.nisar.cli:main",
        )


if __name__ == "__main__":
    unittest.main()
