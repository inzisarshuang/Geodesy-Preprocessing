"""Integration tests for staged Sentinel-1 GAMMA command execution."""

from __future__ import annotations

import json
import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geodesy_preprocessing.sar.slc_generation.gamma.tops.plan import build_slc_plan
from geodesy_preprocessing.sar.slc_generation.gamma.tops.runner import (
    SlcBatchResult,
    SlcCommandError,
    SlcRunner,
    parse_range_samples,
    run_slc_batch,
)


class SlcRunnerTests(unittest.TestCase):
    """Exercise real child processes and literal GAMMA-compatible argv."""

    def _plan(self, root: Path, *, workflow_flag: int):
        raw = root / "RAW"
        raw.mkdir()
        output = root / "SLC"
        images = root / "images"
        images.write_text("20240905\n", encoding="ascii")
        burst = root / "ref_burst_number_table"
        burst.write_text("iw1_number_of_bursts: 1\n", encoding="ascii")
        if workflow_flag == 1:
            (raw / "S1A_20240905_A.SAFE").mkdir()
            (raw / "S1A_20240905_B.SAFE").mkdir()
        else:
            (raw / "S1A_20240905_A.zip").write_bytes(b"a")
            (raw / "S1A_20240905_B.zip").write_bytes(b"b")
        return build_slc_plan(
            raw, output, "-", images, burst, 4, 1, "-", workflow_flag
        )

    def _runner(self, stages: list[str]) -> SlcRunner:
        fixture = Path(__file__).parent / "fixtures" / "fake_gamma_command.py"
        prefix = lambda stage: (sys.executable, str(fixture), stage)
        return SlcRunner(
            command_prefixes={
                "safe_import": prefix("safe_import"),
                "zip_import": prefix("zip_import"),
                "multi_look": prefix("multi_look"),
                "preview": prefix("preview"),
            },
            stage_sink=stages.append,
        )

    def test_safe_mode_uses_absolute_sorted_list_and_literal_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            staging = root / "stage"
            staging.mkdir()
            stages: list[str] = []

            products = self._runner(stages).run_item(plan, plan.items[0], staging)

            invocations = [
                json.loads(line)
                for line in (staging / "invocations.jsonl").read_text().splitlines()
            ]
            safe = invocations[0]
            self.assertEqual(safe["stage"], "safe_import")
            self.assertEqual(safe["cwd"], str(staging))
            self.assertEqual(
                safe["argv"],
                [
                    str(staging / "dir_list"),
                    "--burst_sel",
                    str(plan.burst_table),
                    "--root_name",
                    "20240905",
                    "--out_dir",
                    ".",
                ],
            )
            self.assertEqual(
                (staging / "dir_list").read_text().splitlines(),
                [str(path) for path in plan.items[0].sources],
            )
            self.assertEqual(stages, ["import", "multi_look", "preview"])
            self.assertEqual(
                {path.name for path in products},
                {
                    "20240905.vv.SLC_tab",
                    "20240905.vv.mli",
                    "20240905.vv.mli.par",
                    "20240905.vv.mli.bmp",
                },
            )
            self.assertEqual(invocations[1]["argv"][-4:], ["4", "1", "1", "-"])
            self.assertEqual(invocations[2]["argv"], ["20240905.vv.mli", "123"])

    def test_commands_prefer_the_configured_python_directory_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            staging = root / "stage"
            staging.mkdir()
            fixture = Path(__file__).parent / "fixtures" / "fake_gamma_command.py"
            prefixes = {
                stage: (sys.executable, str(fixture), stage)
                for stage in ("safe_import", "multi_look", "preview")
            }
            runner = SlcRunner(
                python_bin="/chosen/env/bin/python",
                command_prefixes=prefixes,
            )

            runner.run_item(plan, plan.items[0], staging)

            invocation = json.loads(
                (staging / "invocations.jsonl").read_text().splitlines()[0]
            )
            self.assertEqual(invocation["path"].split(os.pathsep)[0], "/chosen/env/bin")

    def test_zip_mode_preserves_legacy_import_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=0)
            staging = root / "stage"
            staging.mkdir()
            with patch.dict(os.environ, {"FAKE_ITEM": "20240905"}):
                self._runner([]).run_item(plan, plan.items[0], staging)

            invocation = json.loads(
                (staging / "invocations.jsonl").read_text().splitlines()[0]
            )
            self.assertEqual(invocation["stage"], "zip_import")
            self.assertEqual(
                invocation["argv"],
                [
                    "zip_list",
                    "ref_burst_number_table",
                    "-",
                    "0",
                    "0",
                    "-",
                    "1",
                    "1",
                ],
            )
            self.assertEqual(
                (staging / "zip_list").read_text().splitlines(),
                [str(path) for path in plan.items[0].sources],
            )

    def test_command_failure_stops_later_stages_and_reports_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            staging = root / "stage"
            staging.mkdir()
            with patch.dict(os.environ, {"FAKE_FAIL_STAGE": "multi_look"}):
                with self.assertRaisesRegex(SlcCommandError, "multi_look.*17"):
                    self._runner([]).run_item(plan, plan.items[0], staging)

            stages = [
                json.loads(line)["stage"]
                for line in (staging / "invocations.jsonl").read_text().splitlines()
            ]
            self.assertEqual(stages, ["safe_import", "multi_look"])


class RangeSamplesTests(unittest.TestCase):
    """Reject ambiguous or invalid GAMMA MLI widths."""

    def test_reads_one_positive_range_sample_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mli.par"
            path.write_text("range_samples: 123\nazimuth_lines: 45\n", encoding="ascii")
            self.assertEqual(parse_range_samples(path), 123)

    def test_rejects_missing_duplicate_non_integer_and_zero_width(self) -> None:
        for name, content in {
            "missing": "azimuth_lines: 2\n",
            "duplicate": "range_samples: 2\nrange_samples: 3\n",
            "non_integer": "range_samples: x\n",
            "zero": "range_samples: 0\n",
        }.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "mli.par"
                path.write_text(content, encoding="ascii")
                with self.assertRaises(ValueError):
                    parse_range_samples(path)


class SlcBatchTests(unittest.TestCase):
    """Publish only complete items and emit Workflow-compatible progress."""

    def _plan(self, root: Path, *, workflow_flag: int):
        return SlcRunnerTests._plan(self, root, workflow_flag=workflow_flag)

    def _runner(self, stages: list[str]) -> SlcRunner:
        return SlcRunnerTests._runner(self, stages)

    def test_success_publishes_one_complete_directory_and_valid_events(self) -> None:
        from geodesy_workflow.progress import ProgressTracker, parse_progress_line

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            raw_fingerprint = sorted(path.name for path in plan.raw_dir.iterdir())
            lines: list[str] = []

            result = run_slc_batch(
                plan,
                runner=self._runner([]),
                progress_sink=lines.append,
            )

            self.assertIsInstance(result, SlcBatchResult)
            self.assertEqual((result.success, result.skipped, result.failed), (1, 0, 0))
            destination = plan.items[0].output_dir
            self.assertTrue((destination / "20240905.vv.mli").is_file())
            self.assertEqual(list(destination.glob("*.burst_number_table")), [])
            self.assertEqual(list(destination.glob("read_S1_TOPS_SLC.*.log")), [])
            self.assertEqual(sorted(path.name for path in plan.raw_dir.iterdir()), raw_fingerprint)
            self.assertEqual(list(plan.slc_dir.glob(".*.stage")), [])
            tracker = ProgressTracker("PRE_GAMMA_SAR_TOPS_SLC")
            events = [parse_progress_line(line) for line in lines]
            self.assertTrue(all(event is not None for event in events))
            for event in events:
                assert event is not None
                tracker.accept(event)
            self.assertEqual(tracker.summary()["success"], 1)
            self.assertEqual(
                [event.stage for event in events if event is not None],
                ["discover", "import", "multi_look", "preview", "publish", "publish"],
            )

    def test_complete_item_is_skipped_without_running_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initial = self._plan(root, workflow_flag=1)
            destination = initial.items[0].output_dir
            destination.mkdir(parents=True)
            (destination / "20240905.vv.mli").write_bytes(b"existing")
            plan = build_slc_plan(
                initial.raw_dir,
                initial.slc_dir,
                "-",
                initial.images_file,
                initial.burst_table,
                4,
                1,
                "-",
                1,
            )
            lines: list[str] = []

            result = run_slc_batch(
                plan, runner=self._runner([]), progress_sink=lines.append
            )

            self.assertEqual((result.success, result.skipped), (0, 1))
            self.assertEqual((destination / "20240905.vv.mli").read_bytes(), b"existing")
            self.assertFalse((destination / "invocations.jsonl").exists())
            self.assertIn('"status":"skipped"', lines[-1])

    def test_incomplete_destination_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            destination = plan.items[0].output_dir
            destination.mkdir(parents=True)
            marker = destination / "keep.txt"
            marker.write_text("unchanged", encoding="ascii")

            with self.assertRaisesRegex(FileExistsError, "incomplete"):
                run_slc_batch(plan, runner=self._runner([]), progress_sink=lambda line: None)

            self.assertEqual(marker.read_text(encoding="ascii"), "unchanged")
            self.assertEqual(list(plan.slc_dir.glob(".*.stage")), [])

    def test_command_and_publication_failures_leave_no_final_partial_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            with patch.dict(os.environ, {"FAKE_FAIL_STAGE": "multi_look"}):
                with self.assertRaises(SlcCommandError):
                    run_slc_batch(
                        plan, runner=self._runner([]), progress_sink=lambda line: None
                    )
            self.assertFalse(plan.items[0].output_dir.exists())
            self.assertEqual(list(plan.slc_dir.glob(".*.stage")), [])

    def test_failed_staging_moves_to_log_across_filesystems_without_masking_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            run_dir = root / "project" / "LOG" / "PRE" / "run"
            run_dir.mkdir(parents=True)
            environment = {
                "FAKE_FAIL_STAGE": "multi_look",
                "GEODESY_RUN_DIR": str(run_dir),
            }
            cross_device = OSError(errno.EXDEV, "Invalid cross-device link")

            with patch.dict(os.environ, environment), patch(
                "geodesy_preprocessing.sar.slc_generation.gamma.tops."
                "runner.os.replace",
                side_effect=cross_device,
            ):
                with self.assertRaises(SlcCommandError):
                    run_slc_batch(
                        plan, runner=self._runner([]), progress_sink=lambda line: None
                    )

            diagnostics = list((run_dir / "diagnostics").glob("*.stage"))
            self.assertEqual(len(diagnostics), 1)
            self.assertFalse(plan.items[0].output_dir.exists())

    def test_keyboard_interrupt_emits_failure_and_removes_active_staging(self) -> None:
        class InterruptingRunner:
            def run_item(self, plan, item, staging, *, stage_sink=None):
                assert stage_sink is not None
                stage_sink("import")
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            lines: list[str] = []

            with self.assertRaises(KeyboardInterrupt):
                run_slc_batch(
                    plan,
                    runner=InterruptingRunner(),  # type: ignore[arg-type]
                    progress_sink=lines.append,
                )

            self.assertEqual(list(plan.slc_dir.glob(".*.stage")), [])
            self.assertIn('"status":"failed"', lines[-1])

    def test_publication_failure_leaves_no_final_partial_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = self._plan(root, workflow_flag=1)
            with patch(
                "geodesy_preprocessing.sar.slc_generation.gamma.tops."
                "runner.os.replace",
                side_effect=OSError("publish failed"),
            ):
                with self.assertRaisesRegex(OSError, "publish failed"):
                    run_slc_batch(
                        plan, runner=self._runner([]), progress_sink=lambda line: None
                    )
            self.assertFalse(plan.items[0].output_dir.exists())
            self.assertEqual(list(plan.slc_dir.glob(".*.stage")), [])


if __name__ == "__main__":
    unittest.main()
