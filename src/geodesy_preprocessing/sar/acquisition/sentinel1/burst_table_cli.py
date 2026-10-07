"""Backend-neutral legacy-compatible Sentinel-1 burst-table command.

功能概述:
    解析原脚本的位置参数，调用可复用 burst 计算接口，并以可回滚的多文件事务
    发布表格和可选覆盖图。

函数说明:
    ``main`` 执行当前工作目录中的 burst 表生成流程并返回进程退出码。
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Sequence

from .burst_table import (
    build_reference_burst_table,
    full_burst_roi,
    load_roi,
    read_burst_number_table,
    select_bursts,
    write_burst_itab,
    write_reference_burst_table,
)

_replace = os.replace


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m geodesy_preprocessing.sar.acquisition.sentinel1.burst_table_cli"
    )
    parser.add_argument("ROI_file")
    parser.add_argument("image_FLAG", nargs="?", default=0, type=int, choices=(0, 1))
    parser.add_argument("name_suffix", nargs="?", default="")
    return parser


def _with_suffix(base: str, suffix: str, extension: str = "") -> str:
    suffix_part = f"_{suffix}" if suffix else ""
    return f"{base}{suffix_part}{extension}"


def _unused_sibling(path: Path, label: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=f".{label}", dir=path.parent
    )
    os.close(descriptor)
    candidate = Path(name)
    candidate.unlink()
    return candidate


def _publish_staged(staged: dict[Path, Path]) -> None:
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for destination in staged:
            if destination.exists():
                backup = _unused_sibling(destination, "backup")
                _replace(destination, backup)
                backups[destination] = backup
        for destination, temporary in staged.items():
            _replace(temporary, destination)
            published.append(destination)
    except BaseException:
        for destination in published:
            destination.unlink(missing_ok=True)
        for destination, backup in backups.items():
            if backup.exists():
                _replace(backup, destination)
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def main(arguments: Sequence[str] | None = None) -> int:
    """Generate burst and reference tables with legacy positional arguments."""

    parsed = _parser().parse_args(arguments)
    working_directory = Path.cwd()
    suffix = parsed.name_suffix
    iw_files = {
        swath: path
        for swath in (1, 2, 3)
        if (
            path := working_directory
            / _with_suffix(f"IW_{swath}", suffix, ".txt")
        ).is_file()
    }
    if parsed.ROI_file == "-":
        print('Using all burst areas as ROI (input "-" detected)')
        roi = full_burst_roi(iw_files)
    else:
        roi = load_roi(parsed.ROI_file)
    selections = select_bursts(iw_files, roi)
    source_rows = read_burst_number_table(
        working_directory / _with_suffix("Old_burst_number_table", suffix)
    )
    reference_rows = build_reference_burst_table(selections, source_rows)

    destinations = {
        "itab": working_directory / _with_suffix("burst_itab", suffix),
        "reference": working_directory
        / _with_suffix("ref_burst_number_table", suffix),
    }
    itab_stage = _unused_sibling(destinations["itab"], "stage")
    reference_stage = _unused_sibling(destinations["reference"], "stage")
    staged: dict[Path, Path] = {
        destinations["itab"]: itab_stage,
        destinations["reference"]: reference_stage,
    }
    try:
        write_burst_itab(selections, itab_stage)
        write_reference_burst_table(reference_rows, reference_stage)
        if parsed.image_FLAG == 0:
            from .coverage import save_coverage

            coverage = working_directory / _with_suffix(
                "Coverage_Python", suffix, ".jpg"
            )
            coverage_stage = _unused_sibling(coverage, "stage")
            staged[coverage] = coverage_stage
            save_coverage(iw_files, roi, coverage_stage)
    except BaseException:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        raise
    _publish_staged(staged)
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
