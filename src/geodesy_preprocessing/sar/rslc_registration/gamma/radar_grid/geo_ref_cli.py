"""GAMMA 普通雷达坐标参考几何命令行入口。

功能概述:
    从项目 CFG 选择一个 case，支持只读预检查或正式执行事务式参考几何流程。

函数说明:
    ``main`` 提供 ``geodesy-pre-gamma-sar-geo-ref`` 正式命令。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .geo_ref_config import (
    load_gamma_radar_geo_ref_config,
    select_gamma_radar_geo_ref_case,
)
from .geo_ref_plan import build_gamma_radar_geo_ref_plan
from .geo_ref_runner import (
    GammaRadarGeoRefCommandError,
    run_gamma_radar_geo_ref,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodesy-pre-gamma-sar-geo-ref",
        description="Build GAMMA reference geometry for an ordinary radar-grid SLC stack.",
    )
    parser.add_argument("--config", required=True, type=Path, help="project CFG path")
    parser.add_argument("--case", help="named [case:name] to execute")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the plan without running GAMMA",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate or execute one configured GAMMA radar reference geometry."""

    options = _parser().parse_args(arguments)
    try:
        case = select_gamma_radar_geo_ref_case(
            load_gamma_radar_geo_ref_config(options.config), options.case
        )
        plan = build_gamma_radar_geo_ref_plan(case)
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if options.dry_run:
        print(
            json.dumps(
                {
                    "case": case.name,
                    "reference_date": case.reference_date,
                    "primary_polarization": case.primary_polarization,
                    "output_dir": str(case.output_dir),
                    "status": "skipped" if plan.skip else "ready",
                    "commands": ["multi_look", "geocoding.py"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    try:
        run_gamma_radar_geo_ref(plan)
    except KeyboardInterrupt:
        print("ERROR: GAMMA radar reference geometry interrupted", file=sys.stderr)
        return 130
    except (
        FileNotFoundError,
        FileExistsError,
        OSError,
        ValueError,
        GammaRadarGeoRefCommandError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
