"""GAMMA 普通雷达坐标 SLC 配准命令行入口。

功能概述:
    从项目 CFG 选择一个单频 case，支持只读查看逐景计划或正式运行配准与发布。

函数说明:
    ``main`` 提供 ``geodesy-pre-gamma-sar-coreg`` 正式命令。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .coreg_config import (
    load_gamma_radar_coreg_config,
    select_gamma_radar_coreg_case,
)
from .coreg_plan import build_gamma_radar_coreg_plan
from .coreg_runner import (
    GammaRadarCoregCommandError,
    run_gamma_radar_coreg,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodesy-pre-gamma-sar-coreg",
        description="Coregister an ordinary GAMMA radar-grid SLC stack.",
    )
    parser.add_argument("--config", required=True, type=Path, help="project CFG path")
    parser.add_argument("--case", help="named [case:name] to execute")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the scene plan without running GAMMA",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate or execute one configured GAMMA radar-grid registration stack."""

    options = _parser().parse_args(arguments)
    try:
        case = select_gamma_radar_coreg_case(
            load_gamma_radar_coreg_config(options.config), options.case
        )
        plan = build_gamma_radar_coreg_plan(case)
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if options.dry_run:
        print(
            json.dumps(
                {
                    "case": case.name,
                    "sensor": case.sensor,
                    "reference_date": case.reference_date,
                    "dates": list(plan.dates),
                    "frequency": {
                        "label": case.frequency,
                        "center_hz": plan.center_frequency_hz,
                    },
                    "primary_polarization": case.primary_polarization,
                    "selected_polarizations": list(case.selected_polarizations),
                    "refinement": case.refinement,
                    "sar_mode": case.sar_mode,
                    "scene_count": len(plan.scenes),
                    "output_dir": str(case.output_dir),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    try:
        run_gamma_radar_coreg(plan)
    except KeyboardInterrupt:
        print("ERROR: GAMMA radar-grid coregistration interrupted", file=sys.stderr)
        return 130
    except (
        FileNotFoundError,
        FileExistsError,
        OSError,
        ValueError,
        GammaRadarCoregCommandError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
