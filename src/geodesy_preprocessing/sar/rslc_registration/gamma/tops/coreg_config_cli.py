"""Project-configured GAMMA TOPS coregistration command line.

功能概述:
    选择项目配置中的具名 case，并委托唯一的 S1-05 正式 CLI。

函数说明:
    ``main`` 提供正式入口；``legacy_main`` 保留旧命令和任务标识。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .coreg_cli import legacy_main as legacy_coreg_main
from .coreg_cli import main as coreg_main
from .coreg_config import coreg_case_arguments, load_coreg_config, select_coreg_case


def _main(arguments: Sequence[str] | None, *, legacy: bool) -> int:
    parser = argparse.ArgumentParser(
        prog=(
            "geodesy-pre-s1-coreg"
            if legacy
            else "geodesy-pre-gamma-sar-tops-coreg"
        )
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--case")
    options = parser.parse_args(arguments)
    try:
        case = select_coreg_case(load_coreg_config(options.config), options.case)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    target = legacy_coreg_main if legacy else coreg_main
    return target(coreg_case_arguments(case))


def main(arguments: Sequence[str] | None = None) -> int:
    """Execute one selected GAMMA TOPS coregistration case."""

    return _main(arguments, legacy=False)


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Execute one case through the historical Sentinel-1 command."""

    return _main(arguments, legacy=True)


__all__ = ["legacy_main", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
