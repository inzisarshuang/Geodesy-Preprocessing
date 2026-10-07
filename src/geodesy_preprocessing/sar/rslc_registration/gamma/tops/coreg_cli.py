"""Command line for GAMMA TOPS stack coregistration.

功能概述:
    保留历史 S1-05 七个位置参数，同时允许显式覆盖配准阈值和桥接策略，构建
    安全计划并执行 staged GAMMA 配准。

函数说明:
    ``main`` 提供正式入口；``legacy_main`` 保留 S1-05 任务标识。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from geodesy_preprocessing.common.progress import PROGRESS_PREFIX

from .coreg_plan import build_coreg_plan
from .coreg_runner import CoregCommandError, run_coreg


def _parser(*, legacy: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="S1-05coreg" if legacy else "GAMMA-TOPS-coreg",
        description="Register a GAMMA TOPS SLC stack.",
    )
    parser.add_argument("slc_dir")
    parser.add_argument("dem_geocode_dir")
    parser.add_argument("output_dir")
    parser.add_argument("reference")
    parser.add_argument("images")
    parser.add_argument("range_looks", type=int)
    parser.add_argument("azimuth_looks", type=int)
    parser.add_argument("--cc", type=float, default=0.8)
    parser.add_argument("--fraction", type=float, default=0.01)
    parser.add_argument("--ph-stdev", type=float, default=0.8)
    parser.add_argument("--id-mode", default="date")
    parser.add_argument("--npoly", type=int, default=4)
    parser.add_argument("--bridge-mode", default="none")
    return parser


class _LocalProgress:
    def __call__(self, line: str) -> None:
        payload = json.loads(line[len(PROGRESS_PREFIX) :])
        index = int(payload["index"])
        total = int(payload["total"])
        print(
            f"[{payload['task']}] [{index}/{total} {index / total * 100:.1f}%] "
            f"item={payload['item']} stage={payload['stage']} status={payload['status']}"
        )


def _main(arguments: Sequence[str] | None, *, legacy: bool) -> int:
    parsed = _parser(legacy=legacy).parse_args(arguments)
    try:
        plan = build_coreg_plan(
            parsed.slc_dir,
            parsed.dem_geocode_dir,
            parsed.output_dir,
            parsed.reference,
            parsed.images,
            parsed.range_looks,
            parsed.azimuth_looks,
            coherence_threshold=parsed.cc,
            fraction_threshold=parsed.fraction,
            phase_stdev_threshold=parsed.ph_stdev,
            id_mode=parsed.id_mode,
            npoly=parsed.npoly,
            bridge_mode=parsed.bridge_mode,
        )
        recorded = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
        sink = print if recorded else _LocalProgress()
        default_task = "PRE_S1_COREG" if legacy else "PRE_GAMMA_SAR_TOPS_COREG"
        run_coreg(
            plan,
            task=os.environ.get("GEODESY_TASK", default_task),
            progress_sink=sink,
        )
    except KeyboardInterrupt:
        print("ERROR: Sentinel-1 coregistration interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, FileExistsError, OSError, ValueError, CoregCommandError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the backend-qualified GAMMA TOPS coregistration workflow."""

    return _main(arguments, legacy=False)


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Run the historical S1-05 workflow and task identity."""

    return _main(arguments, legacy=True)


__all__ = ["legacy_main", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
