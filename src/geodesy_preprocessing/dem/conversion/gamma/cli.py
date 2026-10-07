"""Installed command for standalone GAMMA DEM conversion.

功能概述:
    读取项目 CFG，检查输入和输出状态，并独立执行或预览 GAMMA ``srtm2dem``。

函数说明:
    ``build_parser`` 创建命令参数；``main`` 返回稳定退出码并显示易读错误。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import load_gamma_dem_convert_config
from .srtm2dem import run_gamma_dem_convert


def build_parser() -> argparse.ArgumentParser:
    """Build the GAMMA-specific DEM conversion command parser."""

    parser = argparse.ArgumentParser(
        prog="geodesy-pre-gamma-dem-convert",
        description="把已下载的 GeoTIFF DEM 转换为 GAMMA srtm.dem 和参数文件。",
    )
    parser.add_argument("--config", "-c", required=True, type=Path, help="转换 CFG")
    parser.add_argument("--dry-run", action="store_true", help="只检查并显示命令")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one configured conversion and return a stable process exit code."""

    options = build_parser().parse_args(argv)
    try:
        case = load_gamma_dem_convert_config(options.config)
        run_gamma_dem_convert(case, dry_run=options.dry_run)
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
