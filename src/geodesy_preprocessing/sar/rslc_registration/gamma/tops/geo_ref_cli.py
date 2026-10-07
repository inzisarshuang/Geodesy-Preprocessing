"""Command line for GAMMA TOPS reference DEM geocoding.

功能概述:
    保留历史 S1-04 位置参数，构建安全计划并执行 staged GAMMA 几何编码流程。

函数说明:
    ``main`` 提供正式入口；``legacy_main`` 保留 S1-04 任务标识。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from geodesy_preprocessing.common.progress import PROGRESS_PREFIX

from .geo_ref_plan import build_geo_ref_plan
from .geo_ref_runner import GeoRefCommandError, run_geo_ref


def _parser(*, legacy: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="S1-04geo_ref" if legacy else "GAMMA-TOPS-geo-ref",
        description="Build GAMMA TOPS reference geometry.",
    )
    parser.add_argument("reference")
    parser.add_argument("dem_dir")
    parser.add_argument("slc_dir")
    parser.add_argument("output_dir")
    parser.add_argument("range_looks", type=int)
    parser.add_argument("azimuth_looks", type=int)
    parser.add_argument("latitude_oversampling", nargs="?", default="-")
    parser.add_argument("longitude_oversampling", nargs="?", default="-")
    parser.add_argument("reference_flag", nargs="?", type=int, default=1)
    parser.add_argument("incidence_flag", nargs="?", type=int, default=0)
    return parser


class _LocalProgress:
    def __call__(self, line: str) -> None:
        payload = json.loads(line[len(PROGRESS_PREFIX) :])
        print(
            f"[{payload['task']}] [1/1 100.0%] item={payload['item']} "
            f"stage={payload['stage']} status={payload['status']}"
        )


def _main(arguments: Sequence[str] | None, *, legacy: bool) -> int:
    parsed = _parser(legacy=legacy).parse_args(arguments)
    try:
        plan = build_geo_ref_plan(
            parsed.reference,
            parsed.dem_dir,
            parsed.slc_dir,
            parsed.output_dir,
            parsed.range_looks,
            parsed.azimuth_looks,
            parsed.latitude_oversampling,
            parsed.longitude_oversampling,
            parsed.reference_flag,
            parsed.incidence_flag,
        )
        recorded = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
        sink = print if recorded else _LocalProgress()
        default_task = "PRE_S1_GEO_REF" if legacy else "PRE_GAMMA_SAR_TOPS_GEO_REF"
        task = os.environ.get("GEODESY_TASK", default_task)
        run_geo_ref(plan, task=task, progress_sink=sink)
    except KeyboardInterrupt:
        print("ERROR: Sentinel-1 DEM geocoding interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, FileExistsError, OSError, ValueError, GeoRefCommandError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the backend-qualified GAMMA TOPS geometry workflow."""

    return _main(arguments, legacy=False)


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Run the historical S1-04 workflow and task identity."""

    return _main(arguments, legacy=True)


__all__ = ["legacy_main", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
