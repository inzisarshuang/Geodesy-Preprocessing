"""Staged external-command execution for GAMMA Sentinel-1 TOPS SLC generation.

功能概述:
    在独立 staging 目录中调用官方 GAMMA/辅助命令，将 SAFE 或 ZIP 输入转换为
    SLC tab、MLI、参数和预览产品。

函数说明:
    ``SlcCommandError`` 表示带阶段和退出码的命令失败；``CommandResult`` 记录
    已执行命令；``SlcBatchResult`` 汇总批次；``parse_range_samples`` 读取唯一
    正宽度；``SlcRunner`` 构造并执行单景命令链；``run_slc_batch`` 负责逐景
    staging、进度事件与原子发布；``run_gamma_tops_slc`` 是后端明确的正式入口。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.common.reuse import ReuseState, classify_output
from geodesy_preprocessing.sar.acquisition.sentinel1.burst_table import (
    read_burst_number_table,
    write_reference_burst_table,
)

from .plan import SlcBatchPlan, SlcItemPlan


class SlcCommandError(RuntimeError):
    """Failure of one named external SLC stage."""

    def __init__(self, stage: str, exit_code: int, command: Sequence[str]) -> None:
        self.stage = stage
        self.exit_code = exit_code
        self.command = tuple(command)
        super().__init__(f"{stage} command exited with code {exit_code}")


@dataclass(frozen=True)
class CommandResult:
    """One successfully completed external command."""

    stage: str
    command: tuple[str, ...]
    exit_code: int


@dataclass(frozen=True)
class SlcBatchResult:
    """Final counts and published item directories for one successful batch."""

    success: int
    skipped: int
    failed: int
    outputs: tuple[Path, ...]


def parse_range_samples(path: str | Path) -> int:
    """Return the unique positive ``range_samples`` value from a GAMMA par file."""

    source = Path(path)
    values: list[str] = []
    for line in source.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if fields and fields[0] == "range_samples:":
            if len(fields) != 2:
                raise ValueError(f"invalid range_samples line in {source}")
            values.append(fields[1])
    if len(values) != 1:
        raise ValueError(f"expected one range_samples field in {source}")
    try:
        width = int(values[0])
    except ValueError as error:
        raise ValueError(f"range_samples must be an integer in {source}") from error
    if width < 1:
        raise ValueError(f"range_samples must be positive in {source}")
    return width


def _resolved_command(name: str) -> tuple[str, ...]:
    executable = shutil.which(name)
    if executable is None:
        raise FileNotFoundError(f"required command not found in PATH: {name}")
    return (executable,)


def _safe_swaths(source: Path, polarization: str) -> frozenset[int]:
    """Return IW swaths advertised by direct annotation XML files in one SAFE."""

    annotation_dir = source / "annotation"
    polarization_token = f"-{polarization.lower()}-"
    swaths: set[int] = set()
    for annotation in annotation_dir.glob("*.xml"):
        name = annotation.name.lower()
        if polarization_token not in name:
            continue
        swaths.update(index for index in (1, 2, 3) if f"-iw{index}-" in name)
    return frozenset(swaths)


class SlcRunner:
    """Execute the import, multi-look, and preview stages for one planned item."""

    def __init__(
        self,
        *,
        python_bin: str | Path | None = None,
        command_prefixes: Mapping[str, Sequence[str]] | None = None,
        stage_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._python_bin = str(python_bin or sys.executable)
        self._prefixes = {
            key: tuple(str(part) for part in value)
            for key, value in (command_prefixes or {}).items()
        }
        self._stage_sink = stage_sink or (lambda stage: None)

    def _prefix(self, stage: str) -> tuple[str, ...]:
        if stage in self._prefixes:
            return self._prefixes[stage]
        if stage == "safe_import":
            script = shutil.which("read_S1_TOPS_SLC.py")
            if script is None:
                raise FileNotFoundError(
                    "required command not found in PATH: read_S1_TOPS_SLC.py"
                )
            return (self._python_bin, script)
        names = {
            "zip_import": "S1_import_SLC_from_zipfiles",
            "multi_look": "multi_look_ScanSAR",
            "preview": "raspwr",
        }
        return _resolved_command(names[stage])

    def _run(
        self,
        command_key: str,
        arguments: Sequence[str],
        cwd: Path,
        *,
        stage: str | None = None,
        stage_sink: Callable[[str], None] | None = None,
    ) -> CommandResult:
        visible_stage = stage or command_key
        (stage_sink or self._stage_sink)(visible_stage)
        command = self._prefix(command_key) + tuple(str(value) for value in arguments)
        environment = os.environ.copy()
        python_path = Path(self._python_bin)
        if python_path.parent == Path("."):
            resolved_python = shutil.which(self._python_bin)
            python_dir = Path(resolved_python or sys.executable).parent
        else:
            python_dir = python_path.parent
        environment["PATH"] = os.pathsep.join(
            (str(python_dir), environment.get("PATH", ""))
        )
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(
            (
                str(python_dir.parent / "lib"),
                environment.get("LD_LIBRARY_PATH", ""),
            )
        )
        environment["PROJ_DATA"] = str(python_dir.parent / "share" / "proj")
        environment["GDAL_DATA"] = str(python_dir.parent / "share" / "gdal")
        completed = subprocess.run(command, cwd=cwd, check=False, env=environment)
        if completed.returncode != 0:
            raise SlcCommandError(visible_stage, completed.returncode, command)
        return CommandResult(stage=visible_stage, command=command, exit_code=0)

    def run_item(
        self,
        plan: SlcBatchPlan,
        item: SlcItemPlan,
        staging_dir: str | Path,
        *,
        stage_sink: Callable[[str], None] | None = None,
    ) -> tuple[Path, ...]:
        """Run one item's external command chain in an existing staging directory."""

        staging = Path(staging_dir).resolve(strict=True)
        if not staging.is_dir():
            raise ValueError(f"staging path must be a directory: {staging}")
        list_name = "dir_list" if plan.workflow == "safe" else "zip_list"
        list_path = staging / list_name
        list_path.write_text(
            "".join(f"{source}\n" for source in item.sources), encoding="utf-8"
        )

        if plan.workflow == "safe":
            source_swaths = {
                source: _safe_swaths(source, item.polarization)
                for source in item.sources
            }
            known_swaths = tuple(value for value in source_swaths.values() if value)
            heterogeneous = bool(known_swaths) and len(set(known_swaths)) > 1

            def import_arguments(
                source_list: Path, burst_selection: Path
            ) -> list[str]:
                arguments = [
                    str(source_list),
                    "--burst_sel",
                    str(burst_selection),
                    "--root_name",
                    item.item,
                    "--out_dir",
                    ".",
                ]
                if plan.polarization_argument != "-":
                    arguments.extend(("--pol", item.polarization))
                if plan.opod_dir is not None:
                    arguments.extend(("--OPOD_dir", str(plan.opod_dir)))
                return arguments

            if not heterogeneous:
                self._run(
                    "safe_import",
                    import_arguments(list_path, plan.burst_table),
                    staging,
                    stage="import",
                    stage_sink=stage_sink,
                )
            else:
                reference_rows = read_burst_number_table(plan.burst_table)
                available_swaths = set().union(*known_swaths)
                selected_swaths = sorted(set(reference_rows) & available_swaths)
                if not selected_swaths:
                    raise ValueError(
                        "reference burst table has no IW swath present in SAFE inputs"
                    )
                visible_sink = stage_sink or self._stage_sink
                visible_sink("import")
                combined_tab_lines: list[str] = []
                combined_tab = staging / f"{item.item}.{item.polarization}.SLC_tab"
                for swath in selected_swaths:
                    sources = tuple(
                        source
                        for source in item.sources
                        if swath in source_swaths[source]
                    )
                    swath_list = staging / f"dir_list_iw{swath}"
                    swath_list.write_text(
                        "".join(f"{source}\n" for source in sources),
                        encoding="utf-8",
                    )
                    swath_table = staging / f"iw{swath}.ref_burst_number_table"
                    write_reference_burst_table(
                        (reference_rows[swath],), swath_table
                    )
                    self._run(
                        "safe_import",
                        import_arguments(swath_list, swath_table),
                        staging,
                        stage="import",
                        stage_sink=lambda _stage: None,
                    )
                    if not combined_tab.is_file():
                        raise FileNotFoundError(
                            f"GAMMA did not create expected SLC tab: {combined_tab}"
                        )
                    for line in combined_tab.read_text(encoding="utf-8").splitlines():
                        if line and line not in combined_tab_lines:
                            combined_tab_lines.append(line)
                combined_tab.write_text(
                    "".join(f"{line}\n" for line in combined_tab_lines),
                    encoding="utf-8",
                )
        else:
            staged_burst = staging / "ref_burst_number_table"
            shutil.copy2(plan.burst_table, staged_burst)
            self._run(
                "zip_import",
                (
                    list_name,
                    staged_burst.name,
                    plan.polarization_argument,
                    "0",
                    "0",
                    str(plan.opod_dir) if plan.opod_dir is not None else "-",
                    "1",
                    "1",
                ),
                staging,
                stage="import",
                stage_sink=stage_sink,
            )

        stem = f"{item.item}.{item.polarization}"
        slc_tab = staging / f"{stem}.SLC_tab"
        mli = staging / f"{stem}.mli"
        mli_par = staging / f"{stem}.mli.par"
        preview = staging / f"{stem}.mli.bmp"
        self._run(
            "multi_look",
            (
                slc_tab.name,
                mli.name,
                mli_par.name,
                str(plan.range_looks),
                str(plan.azimuth_looks),
                "1",
                "-",
            ),
            staging,
            stage_sink=stage_sink,
        )
        width = parse_range_samples(mli_par)
        self._run(
            "preview", (mli.name, str(width)), staging, stage_sink=stage_sink
        )
        products = (slc_tab, mli, mli_par, preview)
        missing = [path.name for path in products if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                f"SLC stages completed without required products: {', '.join(missing)}"
            )
        return products


def _failed_staging_destination(staging: Path) -> Path | None:
    run_dir_text = os.environ.get("GEODESY_RUN_DIR")
    if not run_dir_text:
        return None
    run_dir = Path(run_dir_text).resolve()
    if not run_dir.is_dir() or "LOG" not in run_dir.parts:
        return None
    destination_dir = run_dir / "diagnostics"
    destination_dir.mkdir(exist_ok=True)
    return destination_dir / staging.name


DEFAULT_GAMMA_TOPS_SLC_TASK = "PRE_GAMMA_SAR_TOPS_SLC"


def _item_signature(plan: SlcBatchPlan, item: SlcItemPlan) -> str:
    sources = []
    for source in item.sources:
        stat = source.stat()
        sources.append((str(source), stat.st_size, stat.st_mtime_ns))
    payload = {
        "item": item.item,
        "sources": sources,
        "burst_table": str(plan.burst_table),
        "range_looks": plan.range_looks,
        "azimuth_looks": plan.azimuth_looks,
        "polarization": item.polarization,
        "workflow": plan.workflow,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_item_products(output: Path, manifest: dict[str, object]) -> bool:
    products = manifest.get("required_products")
    if not isinstance(products, list) or not products:
        return False
    for value in products:
        if not isinstance(value, str):
            return False
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            return False
        candidate = output / relative
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size < 1:
            return False
    return True


def run_gamma_tops_slc(
    plan: SlcBatchPlan,
    *,
    task: str = DEFAULT_GAMMA_TOPS_SLC_TASK,
    progress_sink: Callable[[str], None] = print,
    keep_failed: bool = False,
    runner: SlcRunner | None = None,
) -> SlcBatchResult:
    """Execute ordered items, emit progress, and atomically publish each result."""

    plan.slc_dir.mkdir(parents=True, exist_ok=True)
    executor = runner or SlcRunner()
    success = 0
    skipped = 0
    failed = 0
    outputs: list[Path] = []
    total = len(plan.items)

    for index, item in enumerate(plan.items, start=1):
        current_stage = "discover"

        def send(event: str, status: str, stage: str) -> None:
            progress_sink(
                emit_progress(
                    event=event,
                    task=task,
                    item=item.item,
                    index=index,
                    total=total,
                    stage=stage,
                    status=status,
                )
            )

        send("item_started", "running", current_stage)
        signature = _item_signature(plan, item)
        reuse = classify_output(
            item.output_dir, "completion.json", signature, _validate_item_products
        )
        if reuse.state is ReuseState.COMPLETE:
            skipped += 1
            send("item_finished", "skipped", current_stage)
            outputs.append(item.output_dir)
            continue
        if reuse.state is not ReuseState.ABSENT:
            failed += 1
            send("item_finished", "failed", current_stage)
            raise FileExistsError(reuse.guidance)

        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{item.item}.", suffix=".stage", dir=plan.slc_dir
            )
        )

        def stage_started(stage: str) -> None:
            nonlocal current_stage
            current_stage = stage
            send("stage_started", "running", stage)

        try:
            products = executor.run_item(plan, item, staging, stage_sink=stage_started)
            stage_started("publish")
            for helper in ("dir_list", "zip_list", "ref_burst_number_table"):
                (staging / helper).unlink(missing_ok=True)
            for pattern in (
                "dir_list_iw*",
                "*.ref_burst_number_table",
                "*.burst_number_table",
                "read_S1_TOPS_SLC.*.log",
            ):
                for transient in staging.glob(pattern):
                    transient.unlink()
            required = [path.relative_to(staging).as_posix() for path in products]
            (staging / "completion.json").write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "input_signature": signature,
                        "processing_root": str(plan.slc_dir.parent),
                        "required_products": required,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            if item.output_dir.exists():
                item.output_dir.rmdir()
            os.replace(staging, item.output_dir)
            success += 1
            outputs.append(item.output_dir)
            send("item_finished", "success", "publish")
        except BaseException as error:
            failed += 1
            send("item_finished", "failed", current_stage)
            if staging.exists():
                diagnostic = _failed_staging_destination(staging)
                if diagnostic is not None:
                    try:
                        shutil.move(str(staging), str(diagnostic))
                    except OSError as diagnostic_error:
                        error.add_note(
                            f"failed to retain staging at {diagnostic}: "
                            f"{diagnostic_error}"
                        )
                elif not keep_failed:
                    shutil.rmtree(staging)
            raise

    return SlcBatchResult(
        success=success,
        skipped=skipped,
        failed=failed,
        outputs=tuple(outputs),
    )


run_slc_batch = run_gamma_tops_slc


__all__ = [
    "CommandResult",
    "DEFAULT_GAMMA_TOPS_SLC_TASK",
    "SlcBatchResult",
    "SlcCommandError",
    "SlcRunner",
    "parse_range_samples",
    "run_gamma_tops_slc",
    "run_slc_batch",
]
