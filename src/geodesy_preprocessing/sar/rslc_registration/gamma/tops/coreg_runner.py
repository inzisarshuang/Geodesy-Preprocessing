"""Staged GAMMA execution for TOPS stack coregistration.

功能概述:
    在隔离 SLC 视图和同级 staging 目录中调用 ``ScanSAR_coreg_stack.py``，
    监控逐景完成产品，按明确规则整理当前任务文件，并原子发布完整 RSLC 栈。

函数说明:
    ``CoregCommandError`` 表示外部命令失败；``CoregCommandResult`` 记录命令；
    ``GammaTopsCoregResult``（兼容名 ``CoregBatchResult``）汇总状态；
    ``CoregRunner`` 执行配准；``organize_coreg_outputs`` 整理输出；
    ``run_gamma_tops_coreg``（兼容名 ``run_coreg``）管理发布。
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.sar.common.models import (
    BackendIdentity,
    GeometryType,
    RegisteredStackAsset,
    RegisteredStackManifest,
)
from geodesy_preprocessing.sar.common.stack_manifest import (
    read_stack_manifest,
    write_stack_manifest,
)
from geodesy_preprocessing.sar.rslc_registration.common import RegistrationResult
from geodesy_preprocessing.sar.slc_generation.gamma.tops.view import stage_slc_tabs

from .coreg_plan import CoregPlan


class CoregCommandError(RuntimeError):
    """Failure of the external stack-coregistration command."""

    def __init__(self, exit_code: int, command: Sequence[str]) -> None:
        self.exit_code = exit_code
        self.command = tuple(command)
        super().__init__(f"coregister command exited with code {exit_code}")


@dataclass(frozen=True)
class CoregCommandResult:
    """One completed external stack command."""

    stage: str
    command: tuple[str, ...]
    exit_code: int


@dataclass(frozen=True)
class GammaTopsCoregResult:
    """Final per-scene counts and published stack directory."""

    success: int
    skipped: int
    failed: int
    output_dir: Path
    registration: RegistrationResult | None = None


CoregBatchResult = GammaTopsCoregResult


def _publish_registration_manifest(plan: CoregPlan, native_root: Path) -> Path:
    manifest = RegisteredStackManifest(
        schema_version=2,
        status="complete",
        sensor="sentinel1",
        source_product_type="SLC",
        backend=BackendIdentity("gamma", "unknown"),
        geometry_type=GeometryType.RADAR_GRID,
        reference_date=plan.reference,
        dates=tuple(item.date for item in plan.items),
        frequencies=("C",),
        polarizations=("VV",),
        assets=tuple(
            RegisteredStackAsset(
                date=item.date,
                polarization="VV",
                data_path=Path("RSLC") / f"{item.date}.rslc",
                parameter_path=Path("RSLC") / f"{item.date}.rslc.par",
            )
            for item in plan.items
        ),
        quality_path=Path("coreg_quality") / "1ID.coreg_quality",
    )
    return write_stack_manifest(manifest, native_root / "registered_stack.json")


def _registration_result(plan: CoregPlan, native_root: Path) -> RegistrationResult:
    manifest_path = native_root / "registered_stack.json"
    manifest = read_stack_manifest(manifest_path)
    return RegistrationResult(
        manifest_path=manifest_path,
        reference_date=manifest.reference_date,
        dates=manifest.dates,
        quality_path=(
            native_root / manifest.quality_path
            if manifest.quality_path is not None
            else None
        ),
        native_root=native_root,
    )


def _prepare_slc_view(plan: CoregPlan, staging: Path) -> tuple[Path, Path]:
    view = staging / "slc_view"
    view.mkdir()
    local_tabs = stage_slc_tabs(
        tuple((item.tab_file, item.tab_rows) for item in plan.items), view
    )
    tabs = {item.date: tab for item, tab in zip(plan.items, local_tabs, strict=True)}
    tab_list = view / "SLC_tab_list"
    tab_list.write_text(
        "".join(f"{tabs[item.date]}\n" for item in plan.items), encoding="utf-8"
    )
    return tabs[plan.reference], tab_list


def _move_owned(source: Path, destination_dir: Path, *, name: str | None = None) -> None:
    destination_dir.mkdir(exist_ok=True)
    destination = destination_dir / (name or source.name)
    if destination.exists():
        raise FileExistsError(f"coregistration output collision: {destination}")
    source.replace(destination)


def organize_coreg_outputs(plan: CoregPlan, staging_dir: str | Path) -> None:
    """Move only known task-owned root products into the legacy RSLC layout."""

    staging = Path(staging_dir).resolve(strict=True)
    rslc_dir = staging / "RSLC"
    quality_dir = staging / "coreg_quality"
    diff_dir = staging / "diff_bmp"
    supplemental_dir = staging / "sup_mtls"
    for directory in (rslc_dir, quality_dir, diff_dir, supplemental_dir):
        directory.mkdir(exist_ok=True)

    for path in tuple(staging.iterdir()):
        if path in {rslc_dir, quality_dir, diff_dir, supplemental_dir}:
            continue
        if path.is_dir():
            if path.name == "slc_view" or path.name.startswith(("tmp_dir", "tmp_coreg_")):
                shutil.rmtree(path)
            continue
        name = path.name
        if name == "ID.coreg_quality":
            _move_owned(path, quality_dir, name="1ID.coreg_quality")
        elif name.endswith((".coreg_quality", ".results")):
            _move_owned(path, quality_dir)
        elif name.endswith(".diff.bmp"):
            _move_owned(path, diff_dir)
        elif ".rslc.iw" in name:
            _move_owned(path, supplemental_dir)
        elif fnmatch.fnmatch(name, "*.rslc*"):
            _move_owned(path, rslc_dir)
        elif (
            fnmatch.fnmatch(name, "*.mli")
            or fnmatch.fnmatch(name, "*.mli.*")
            or name.endswith(".diff")
            or "slc" in name
            or "SLC_tab" in name
            or name.endswith(".off")
            or name.endswith(".sim_unw")
            or name.endswith(".rmli")
        ):
            _move_owned(path, supplemental_dir)

    missing = []
    for expected in plan.expected_outputs:
        staged = staging / expected.relative_to(plan.output_dir)
        if not staged.is_file() or staged.stat().st_size == 0:
            missing.append(str(expected.relative_to(plan.output_dir)))
    if missing:
        raise FileNotFoundError(
            f"coregistration completed without required products: {', '.join(missing)}"
        )


class CoregRunner:
    """Execute one complete ScanSAR/TOPS stack in an isolated staging directory."""

    def __init__(self, *, python_bin: str | Path | None = None, poll_seconds: float = 0.5) -> None:
        self._python_bin = Path(python_bin or sys.executable)
        self._poll_seconds = poll_seconds

    def _completed_dates(self, plan: CoregPlan, cwd: Path) -> tuple[str, ...]:
        completed: list[str] = []
        reference_files = (cwd / f"{plan.reference}.rslc", cwd / f"{plan.reference}.rslc.par")
        if all(path.is_file() and path.stat().st_size > 0 for path in reference_files):
            completed.append(plan.reference)
        if plan.id_mode == "date":
            for item in sorted(plan.items, key=lambda value: value.date):
                if item.date == plan.reference:
                    continue
                pair = (
                    cwd / f"{plan.reference}_{item.date}.coreg_quality",
                    cwd / f"{plan.reference}_{item.date}.results",
                )
                if all(path.is_file() and path.stat().st_size > 0 for path in pair):
                    completed.append(item.date)
        return tuple(completed)

    def _execute(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        plan: CoregPlan,
        item_sink: Callable[[str], None],
    ) -> CoregCommandResult:
        executable = shutil.which(command[0])
        if executable is None:
            raise FileNotFoundError(f"required command not found in PATH: {command[0]}")
        resolved = (executable, *(str(value) for value in command[1:]))
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join(
            (str(self._python_bin.parent), environment.get("PATH", ""))
        )
        process = subprocess.Popen(resolved, cwd=cwd, env=environment)
        reported: set[str] = set()
        while process.poll() is None:
            for date in self._completed_dates(plan, cwd):
                if date not in reported:
                    item_sink(date)
                    reported.add(date)
            time.sleep(self._poll_seconds)
        exit_code = process.wait()
        for date in self._completed_dates(plan, cwd):
            if date not in reported:
                item_sink(date)
                reported.add(date)
        if exit_code != 0:
            raise CoregCommandError(exit_code, resolved)
        return CoregCommandResult("coregister", tuple(resolved), 0)

    def run_stack(
        self,
        plan: CoregPlan,
        staging_dir: str | Path,
        *,
        item_sink: Callable[[str], None],
    ) -> CoregCommandResult:
        """Prepare the read-only view, run GAMMA, organize, and verify outputs."""

        staging = Path(staging_dir).resolve(strict=True)
        reference_tab, tab_list = _prepare_slc_view(plan, staging)
        command: list[str] = [
            "ScanSAR_coreg_stack.py",
            str(reference_tab),
            str(tab_list),
            str(plan.height_file),
            str(plan.range_looks),
            str(plan.azimuth_looks),
            "--ID", plan.id_mode,
            "--summary", "ID.coreg_quality",
            "--cc", str(plan.coherence_threshold),
            "--fraction", str(plan.fraction_threshold),
            "--ph_stdev", str(plan.phase_stdev_threshold),
            "--wdir", str(staging),
            "--npoly", str(plan.npoly),
            "--no_cleaning",
            "--no_check",
        ]
        if plan.bridge_mode != "none":
            command.extend(("--sec_ref", plan.bridge_mode))
        reported: set[str] = set()

        def report_once(date: str) -> None:
            if date not in reported:
                item_sink(date)
                reported.add(date)

        result = self._execute(command, staging, plan=plan, item_sink=report_once)
        (staging / "SLC_tab_list").write_text(
            "".join(f"{item.tab_file}\n" for item in plan.items), encoding="utf-8"
        )
        organize_coreg_outputs(plan, staging)
        reported_products = {
            item.date
            for item in plan.items
            if (staging / "RSLC" / f"{item.date}.rslc").is_file()
        }
        for item in (plan.reference, *(value.date for value in sorted(plan.items, key=lambda value: value.date) if value.date != plan.reference)):
            if item in reported_products:
                report_once(item)
        return result


def _failed_staging_destination(staging: Path) -> Path | None:
    run_dir_text = os.environ.get("GEODESY_RUN_DIR")
    if not run_dir_text:
        return None
    run_dir = Path(run_dir_text).resolve()
    if not run_dir.is_dir() or "LOG" not in run_dir.parts:
        return None
    diagnostics = run_dir / "diagnostics"
    diagnostics.mkdir(exist_ok=True)
    return diagnostics / staging.name


DEFAULT_GAMMA_TOPS_COREG_TASK = "PRE_GAMMA_SAR_TOPS_COREG"


def run_gamma_tops_coreg(
    plan: CoregPlan,
    *,
    task: str = DEFAULT_GAMMA_TOPS_COREG_TASK,
    progress_sink: Callable[[str], None] = print,
    runner: CoregRunner | None = None,
) -> CoregBatchResult:
    """Execute or skip a stack with per-scene progress and atomic publication."""

    order = (plan.reference,) + tuple(
        item.date for item in sorted(plan.items, key=lambda value: value.date) if item.date != plan.reference
    )

    def send(event: str, date: str, index: int, status: str, stage: str) -> None:
        progress_sink(
            emit_progress(
                event=event,
                task=task,
                item=date,
                index=index,
                total=len(order),
                stage=stage,
                status=status,
            )
        )

    if plan.skip:
        for index, date in enumerate(order, 1):
            send("item_started", date, index, "running", "discover")
            send("item_finished", date, index, "skipped", "discover")
        manifest_path = plan.output_dir / "registered_stack.json"
        return CoregBatchResult(
            0,
            len(order),
            0,
            plan.output_dir,
            (
                _registration_result(plan, plan.output_dir)
                if manifest_path.is_file()
                else None
            ),
        )

    plan.output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{plan.output_dir.name}.", suffix=".stage", dir=plan.output_dir.parent
        )
    )
    completed: set[str] = set()

    def item_completed(date: str) -> None:
        if date in completed or date not in order:
            return
        index = order.index(date) + 1
        send("item_started", date, index, "running", "coregister")
        send("item_finished", date, index, "success", "coregister")
        completed.add(date)

    try:
        (runner or CoregRunner()).run_stack(plan, staging, item_sink=item_completed)
        if plan.output_dir.exists():
            raise FileExistsError(
                f"RSLC destination appeared during execution: {plan.output_dir}"
            )
        _publish_registration_manifest(plan, staging)
        os.replace(staging, plan.output_dir)
        return CoregBatchResult(
            len(completed),
            0,
            0,
            plan.output_dir,
            _registration_result(plan, plan.output_dir),
        )
    except BaseException as error:
        pending = next((date for date in order if date not in completed), order[-1])
        send("item_finished", pending, order.index(pending) + 1, "failed", "coregister")
        if staging.exists():
            diagnostic = _failed_staging_destination(staging)
            if diagnostic is None:
                shutil.rmtree(staging)
            else:
                try:
                    shutil.move(str(staging), str(diagnostic))
                except OSError as diagnostic_error:
                    error.add_note(f"failed to retain staging at {diagnostic}: {diagnostic_error}")
        raise


run_coreg = run_gamma_tops_coreg


__all__ = [
    "DEFAULT_GAMMA_TOPS_COREG_TASK",
    "GammaTopsCoregResult",
    "CoregBatchResult",
    "CoregCommandError",
    "CoregCommandResult",
    "CoregRunner",
    "organize_coreg_outputs",
    "run_gamma_tops_coreg",
    "run_coreg",
]
