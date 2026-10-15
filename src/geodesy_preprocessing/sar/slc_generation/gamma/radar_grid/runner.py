"""Transactional batch runner for GAMMA radar-grid SLC generation.

功能概述:
    在输出旁建立逐景暂存目录，以无 shell 子进程执行计划，把 stdout/stderr 写入逐景日志，
    验证完整结果后成组发布，并输出统一进度事件。

函数说明:
    ``OtherSarCommandError`` 描述外部命令失败；``SlcRunSummary`` 汇总批次；
    ``run_gamma_radar_slc`` 执行计划；``run_slc_batch`` 是兼容别名。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.case_layout import resolve_case_log_path
from geodesy_preprocessing.common.progress import emit_progress

from .models import CommandSpec
from .publication import publish_images_table, publish_output_group
from .plan import SlcBatchPlan


class OtherSarCommandError(RuntimeError):
    """External command failure with stage, exit code, and log location."""

    def __init__(
        self, stage: str, returncode: int, command: tuple[str, ...], stderr_log: Path
    ) -> None:
        self.stage = stage
        self.returncode = returncode
        self.command = command
        self.stderr_log = stderr_log
        tail = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
        detail = f": {tail.splitlines()[-1]}" if tail else ""
        super().__init__(
            f"Other-SAR stage {stage} failed with exit code {returncode}{detail}; "
            f"stderr log: {stderr_log}"
        )


@dataclass(frozen=True)
class SlcRunSummary:
    """Counts for a successfully concluded Other-SAR SLC batch."""

    total: int
    completed: int
    skipped: int
    failed: int


def _range_samples(path: Path) -> int:
    values: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        fields = line.split()
        if fields and fields[0] == "range_samples:":
            if len(fields) != 2:
                raise ValueError(f"invalid range_samples line in {path}")
            values.append(fields[1])
    if len(values) != 1:
        raise ValueError(f"expected one range_samples field in {path}")
    try:
        width = int(values[0])
    except ValueError as error:
        raise ValueError(f"range_samples must be an integer in {path}") from error
    if width < 1:
        raise ValueError(f"range_samples must be positive in {path}")
    return width


def _resolved_argv(command: CommandSpec) -> tuple[str, ...]:
    if command.parameter_file is None:
        return command.argv
    width = str(_range_samples(command.parameter_file))
    replaced = tuple(width if value == "{range_samples}" else value for value in command.argv)
    if replaced == command.argv:
        raise ValueError(f"stage {command.stage} has parameter_file but no range placeholder")
    return replaced


def _event(
    sink: Callable[[str], None],
    *,
    event: str,
    task: str,
    status: str,
    item: str | None = None,
    index: int | None = None,
    total: int | None = None,
    stage: str | None = None,
) -> None:
    sink(
        emit_progress(
            event=event,
            task=task,
            status=status,
            item=item,
            index=index,
            total=total,
            stage=stage,
        )
    )


def run_gamma_radar_slc(
    plan: SlcBatchPlan,
    *,
    environment: Mapping[str, str] | None = None,
    progress_sink: Callable[[str], None] = print,
) -> SlcRunSummary:
    """Execute all scenes, stopping on failure while retaining earlier scenes."""

    case = plan.case
    total = len(plan.products)
    task = case.workflow_task
    publish_images_table(case.images_file, tuple(product.date for product in plan.products))
    _event(progress_sink, event="batch_started", task=task, status="start", total=total)
    completed = 0
    skipped = 0
    run_environment = os.environ.copy()
    if environment:
        run_environment.update((str(key), str(value)) for key, value in environment.items())
    staging_parent = case.slc_dir.parent
    staging_parent.mkdir(parents=True, exist_ok=True)

    for index, product in enumerate(plan.products, start=1):
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{case.sensor}-{product.date}-", suffix=".stage", dir=staging_parent
            )
        )
        log_dir = (
            resolve_case_log_path(case.case_root, task, case.name, product.date)
            if case.case_root is not None
            else staging_parent / "LOG/PRE" / task / case.name / product.date
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / "stdout.log"
        stderr_log = log_dir / "stderr.log"
        try:
            _event(
                progress_sink,
                event="item_started",
                task=task,
                status="start",
                item=product.date,
                index=index,
                total=total,
                stage="prepare",
            )
            prepared = plan.adapter.prepare(product, staging)
            scene = plan.adapter.build_scene_plan(case, prepared)
            if scene.state == "skip":
                skipped += 1
                _event(
                    progress_sink,
                    event="item_finished",
                    task=task,
                    status="skipped",
                    item=product.date,
                    index=index,
                    total=total,
                    stage="publish",
                )
                continue
            with stdout_log.open("w", encoding="utf-8") as stdout, stderr_log.open(
                "w", encoding="utf-8"
            ) as stderr:
                for command in scene.commands:
                    _event(
                        progress_sink,
                        event="item_progress",
                        task=task,
                        status="running",
                        item=product.date,
                        index=index,
                        total=total,
                        stage=command.stage,
                    )
                    argv = _resolved_argv(command)
                    completed_process = subprocess.run(
                        argv,
                        cwd=staging,
                        env=run_environment,
                        stdout=stdout,
                        stderr=stderr,
                        check=False,
                        text=True,
                        shell=False,
                    )
                    stdout.flush()
                    stderr.flush()
                    if completed_process.returncode != 0:
                        raise OtherSarCommandError(
                            command.stage,
                            completed_process.returncode,
                            argv,
                            stderr_log,
                        )
            plan.adapter.finalize_scene(prepared, scene)
            publish_output_group(staging, scene.outputs)
            completed += 1
            _event(
                progress_sink,
                event="item_finished",
                task=task,
                status="complete",
                item=product.date,
                index=index,
                total=total,
                stage="publish",
            )
        except BaseException:
            _event(
                progress_sink,
                event="item_finished",
                task=task,
                status="failed",
                item=product.date,
                index=index,
                total=total,
                stage="failed",
            )
            _event(
                progress_sink,
                event="batch_finished",
                task=task,
                status="failed",
                index=index,
                total=total,
                stage="failed",
            )
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    _event(
        progress_sink,
        event="batch_finished",
        task=task,
        status="complete",
        index=total,
        total=total,
        stage="complete",
    )
    return SlcRunSummary(total, completed, skipped, 0)


run_slc_batch = run_gamma_radar_slc


__all__ = [
    "OtherSarCommandError",
    "SlcRunSummary",
    "run_gamma_radar_slc",
    "run_slc_batch",
]
