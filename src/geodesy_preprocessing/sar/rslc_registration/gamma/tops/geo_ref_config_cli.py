"""Project-configured GAMMA TOPS reference DEM-geocoding command line.

功能概述:
    选择项目配置中的一个具名 case，并委托唯一的 S1-04 兼容 CLI。

函数说明:
    ``main`` 提供正式入口；``legacy_main`` 保留旧命令和任务标识。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .geo_ref_cli import legacy_main as legacy_geo_ref_main
from .geo_ref_cli import main as geo_ref_main
from .geo_ref_config import geo_ref_case_arguments, load_geo_ref_config, select_geo_ref_case


def _main(arguments: Sequence[str] | None, *, legacy: bool) -> int:
    parser = argparse.ArgumentParser(
        prog=(
            "geodesy-pre-s1-geo-ref"
            if legacy
            else "geodesy-pre-gamma-sar-tops-geo-ref"
        )
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--case")
    options = parser.parse_args(arguments)
    try:
        selected = select_geo_ref_case(load_geo_ref_config(options.config), options.case)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    target = legacy_geo_ref_main if legacy else geo_ref_main
    return target(geo_ref_case_arguments(selected))


def main(arguments: Sequence[str] | None = None) -> int:
    """Execute one selected GAMMA TOPS reference-geometry case."""

    return _main(arguments, legacy=False)


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Execute one case through the historical Sentinel-1 command."""

    return _main(arguments, legacy=True)


__all__ = ["legacy_main", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
