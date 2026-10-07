"""Project-configured GAMMA Sentinel-1 TOPS SLC command line.

功能概述:
    从研究项目的具名 ``.cfg`` case 生成历史 S1-03 参数，并委托唯一的正式
    SLC CLI 执行，从而保持位置参数入口与配置入口行为一致。

函数说明:
    ``main`` 提供 GAMMA TOPS 命令；``legacy_main`` 保留旧命令和任务标识。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .cli import legacy_main as legacy_slc_main
from .cli import main as slc_main
from .config import load_slc_config, select_slc_case, slc_case_arguments


def _parser(*, legacy: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodesy-pre-s1-slc" if legacy else "geodesy-pre-gamma-sar-tops-slc"
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--case")
    return parser


def _main(arguments: Sequence[str] | None, *, legacy: bool) -> int:
    options = _parser(legacy=legacy).parse_args(arguments)

    try:
        cases = load_slc_config(options.config)
        selected = select_slc_case(cases, options.case)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    target = legacy_slc_main if legacy else slc_main
    return target(slc_case_arguments(selected))


def main(arguments: Sequence[str] | None = None) -> int:
    """Execute one selected GAMMA TOPS SLC case."""

    return _main(arguments, legacy=False)


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Execute one case through the historical Sentinel-1 command."""

    return _main(arguments, legacy=True)


__all__ = ["legacy_main", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
