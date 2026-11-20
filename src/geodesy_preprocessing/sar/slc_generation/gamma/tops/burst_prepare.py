"""Automatic reference burst-table preparation for GAMMA Sentinel-1 TOPS.

功能概述:
    当 SLC case 尚无参考 burst 表时，按配置选择参考 SAFE，使用 GAMMA 提取
    burst 覆盖范围，与项目 ROI 求交后发布 SLC 生成所需的表；已有非空表直接复用。

函数说明:
    ``ensure_reference_burst_table`` 为正式配置入口按需准备参考表，并兼容同一天
    被拆成多个、所含 IW 子带不同的 SAFE 数据。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from geodesy_preprocessing.sar.acquisition.sentinel1.burst_table import (
    BurstNumberRow,
    BurstSelection,
    load_roi,
    read_burst_number_table,
    select_bursts,
    write_burst_itab,
    write_reference_burst_table,
)

from .config import GammaTopsSlcCase


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _required_program(value: str | Path | None, name: str) -> str:
    if value is not None:
        return str(value)
    resolved = shutil.which(name)
    if resolved is None:
        raise RuntimeError(f"required GAMMA command is not available: {name}")
    return resolved


def _run(
    command: Sequence[str],
    *,
    runner: CommandRunner,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            list(command), cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise RuntimeError(f"could not start command {command[0]}: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise RuntimeError(f"command failed ({command[0]}): {detail}")
    return result


def _reference_safes(case: GammaTopsSlcCase) -> tuple[Path, ...]:
    if case.workflow_flag != 1:
        raise ValueError(
            "automatic reference burst-table preparation currently requires "
            "workflow = safe; provide an existing burst_table for ZIP input"
        )
    if case.reference_selector is None:
        raise ValueError(
            f"SLC case {case.name!r} needs reference_selector because its "
            "burst_table does not exist"
        )
    candidates = sorted(case.raw_dir.glob("*.SAFE"))
    selected = tuple(
        path for path in candidates if case.reference_selector in path.name
    )
    if not selected:
        raise FileNotFoundError(
            f"no SAFE in {case.raw_dir} matches reference_selector "
            f"{case.reference_selector!r}"
        )
    return selected


def _annotation_files(
    reference_safes: Sequence[Path], polarization: str
) -> tuple[tuple[Path, int, Path], ...]:
    selected: list[tuple[Path, int, Path]] = []
    polarization_token = f"-{polarization.lower()}-"
    for safe in reference_safes:
        for annotation in sorted((safe / "annotation").glob("*.xml")):
            name = annotation.name.lower()
            if polarization_token not in name:
                continue
            swath = next(
                (index for index in (1, 2, 3) if f"-iw{index}-" in name), None
            )
            if swath is not None:
                selected.append((safe, swath, annotation))
    if not selected:
        raise FileNotFoundError(
            "no Sentinel-1 IW annotation XML was found for polarization "
            f"{polarization!r} in the selected reference SAFE data"
        )
    return tuple(selected)


def _burst_rows(output: str, annotation: Path) -> tuple[tuple[float, ...], ...]:
    rows: list[tuple[float, ...]] = []
    for line in output.splitlines():
        fields = line.split()
        if not fields or fields[0] != "Burst:":
            continue
        if len(fields) < 17:
            raise ValueError(f"unexpected S1_burstloc row for {annotation}: {line}")
        try:
            rows.append(tuple(float(value) for value in (fields[2], *fields[9:17])))
        except ValueError as error:
            raise ValueError(
                f"non-numeric S1_burstloc row for {annotation}: {line}"
            ) from error
    if not rows:
        raise ValueError(f"S1_burstloc returned no burst rows for {annotation}")
    return tuple(rows)


def _write_iw_files(
    annotations: Sequence[tuple[Path, int, Path]],
    staging: Path,
    *,
    runner: CommandRunner,
    burstloc: str,
) -> tuple[dict[int, Path], dict[int, tuple[tuple[Path, int], ...]]]:
    rows_by_swath: dict[int, list[tuple[float, ...]]] = {}
    addresses_by_swath: dict[int, list[tuple[Path, int]]] = {}
    for safe, swath, annotation in annotations:
        result = _run([burstloc, str(annotation)], runner=runner, cwd=staging)
        rows = _burst_rows(result.stdout, annotation)
        rows_by_swath.setdefault(swath, []).extend(rows)
        for row in rows:
            local_number = row[0]
            if not local_number.is_integer() or local_number < 1:
                raise ValueError(
                    f"invalid local burst number {local_number} in {annotation}"
                )
            addresses_by_swath.setdefault(swath, []).append(
                (safe, int(local_number))
            )
    paths: dict[int, Path] = {}
    for swath, rows in sorted(rows_by_swath.items()):
        destination = staging / f"IW_{swath}.txt"
        destination.write_text(
            "".join(" ".join(f"{value:.10f}" for value in row) + "\n" for row in rows),
            encoding="ascii",
        )
        paths[swath] = destination
    return paths, {
        swath: tuple(addresses) for swath, addresses in addresses_by_swath.items()
    }


def _source_burst_tables(
    reference_safes: Sequence[Path],
    staging: Path,
    *,
    runner: CommandRunner,
    helper_script: str,
) -> dict[Path, dict[int, BurstNumberRow]]:
    tables: dict[Path, dict[int, BurstNumberRow]] = {}
    for safe in reference_safes:
        command = [
            sys.executable,
            helper_script,
            "1",
            "--dir_ref",
            str(safe),
            "--out_dir",
            str(staging),
        ]
        _run(command, runner=runner, cwd=staging)
        expected = staging / f"{safe.stem}.burst_number_table"
        if not expected.is_file() or expected.stat().st_size == 0:
            raise RuntimeError(
                "GAMMA did not create the expected source burst-number table: "
                f"{expected}"
            )
        tables[safe] = dict(read_burst_number_table(expected))
    return tables


def _reference_rows(
    selections: Sequence[BurstSelection],
    addresses_by_swath: dict[int, tuple[tuple[Path, int], ...]],
    source_tables: dict[Path, dict[int, BurstNumberRow]],
) -> tuple[BurstNumberRow, ...]:
    rows: list[BurstNumberRow] = []
    for selection in selections:
        addresses = addresses_by_swath[selection.swath][
            selection.first_burst - 1 : selection.last_burst
        ]
        absolute_numbers: list[float] = []
        for safe, local_number in addresses:
            try:
                source = source_tables[safe][selection.swath]
            except KeyError as error:
                raise ValueError(
                    f"GAMMA source table for {safe.name} has no IW{selection.swath} row"
                ) from error
            absolute_numbers.append(source.first_burst + local_number - 1)
        rows.append(
            BurstNumberRow(
                selection.swath,
                len(absolute_numbers),
                min(absolute_numbers),
                max(absolute_numbers),
            )
        )
    return tuple(rows)


def _suffix_for(path: Path, case_name: str) -> str:
    prefix = "ref_burst_number_table"
    if path.name.startswith(prefix):
        return path.name.removeprefix(prefix)
    return f"_{case_name}"


def ensure_reference_burst_table(
    case: GammaTopsSlcCase,
    *,
    runner: CommandRunner | None = None,
    burstloc_executable: str | Path | None = None,
    helper_script: str | Path | None = None,
) -> Path:
    """Create ``case.burst_table`` only when no non-empty table exists."""

    destination = case.burst_table
    if destination.is_file() and destination.stat().st_size > 0:
        print(f"Reference burst table already exists; reusing: {destination}")
        return destination
    if case.roi_file is None:
        raise ValueError(
            f"SLC case {case.name!r} needs roi_file because its burst_table "
            "does not exist"
        )
    if not case.roi_file.is_file():
        raise FileNotFoundError(f"ROI file not found: {case.roi_file}")
    if not case.raw_dir.is_dir():
        raise FileNotFoundError(f"Sentinel-1 raw directory not found: {case.raw_dir}")

    command_runner = runner or subprocess.run
    burstloc = _required_program(burstloc_executable, "S1_burstloc")
    helper = _required_program(helper_script, "S1_BURST_tab_from_zipfile.py")
    reference_safes = _reference_safes(case)
    polarization = "vv" if case.polarization == "-" else case.polarization.lower()
    annotations = _annotation_files(reference_safes, polarization)

    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = _suffix_for(destination, case.name)
    itab_destination = destination.parent / f"burst_itab{suffix}"
    coverage_destination = destination.parent / f"Coverage_Python{suffix}.jpg"
    print(
        "Reference burst table is missing; generating it automatically from "
        f"{len(reference_safes)} SAFE director{'y' if len(reference_safes) == 1 else 'ies'}."
    )
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.", dir=destination.parent
    ) as directory:
        staging = Path(directory)
        iw_files, addresses_by_swath = _write_iw_files(
            annotations, staging, runner=command_runner, burstloc=burstloc
        )
        source_tables = _source_burst_tables(
            reference_safes,
            staging,
            runner=command_runner,
            helper_script=helper,
        )
        roi = load_roi(case.roi_file)
        selections = select_bursts(iw_files, roi)
        rows = _reference_rows(selections, addresses_by_swath, source_tables)

        staged_itab = staging / itab_destination.name
        staged_reference = staging / destination.name
        write_burst_itab(selections, staged_itab)
        write_reference_burst_table(rows, staged_reference)
        staged_coverage: Path | None = None
        if case.burst_coverage_plot:
            from geodesy_preprocessing.sar.acquisition.sentinel1.coverage import (
                save_coverage,
            )

            staged_coverage = staging / coverage_destination.name
            save_coverage(iw_files, roi, staged_coverage)

        os.replace(staged_itab, itab_destination)
        if staged_coverage is not None:
            os.replace(staged_coverage, coverage_destination)
        os.replace(staged_reference, destination)
    print(f"Reference burst table generated: {destination}")
    return destination


__all__ = ["ensure_reference_burst_table"]
