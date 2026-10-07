"""Compatible command line for GAMMA Sentinel-1 TOPS SLC generation.

功能概述:
    保留历史 S1-03 位置参数，构建安全批计划并执行 staged GAMMA SLC 流程；在
    Workflow 运行中输出结构化事件，直接运行时显示统一进度。

函数说明:
    ``main`` 提供 GAMMA TOPS 正式入口；``legacy_main`` 保留 S1-03 任务标识。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from geodesy_preprocessing.common.progress import PROGRESS_PREFIX

from .plan import build_slc_plan
from .runner import SlcCommandError, run_slc_batch


def _parser(*, legacy: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="S1-03raw2slc" if legacy else "geodesy-pre-gamma-sar-tops-slc",
        description="Generate GAMMA Sentinel-1 TOPS SLC products.",
    )
    parser.add_argument("RAW_dir")
    parser.add_argument("SLC_dir")
    parser.add_argument("OPOD_dir")
    parser.add_argument("images")
    parser.add_argument("ref_burst_number_table")
    parser.add_argument("rlks", type=int)
    parser.add_argument("azlks", type=int)
    parser.add_argument("optional", nargs="*")
    return parser


def _optional_arguments(values: Sequence[str]) -> tuple[str, int]:
    if len(values) > 2:
        raise ValueError("at most Pol and workflow_flag may follow azlks")
    polarization = "-"
    workflow_flag = 0
    if len(values) == 1:
        if values[0] in {"0", "1"}:
            workflow_flag = int(values[0])
        else:
            polarization = values[0]
    elif len(values) == 2:
        polarization = values[0]
        try:
            workflow_flag = int(values[1])
        except ValueError as error:
            raise ValueError("workflow_flag must be 0 or 1") from error
    return polarization, workflow_flag


class _LocalProgress:
    def __init__(self, *, tty: bool) -> None:
        self._tty = tty
        self._counts = {"success": 0, "skipped": 0, "failed": 0}

    def __call__(self, line: str) -> None:
        payload = json.loads(line[len(PROGRESS_PREFIX) :])
        status = str(payload["status"])
        if payload["event"] == "item_finished":
            self._counts[status] += 1
        index = int(payload["index"])
        total = int(payload["total"])
        percentage = index / total * 100.0
        if not self._tty:
            print(
                f"[{payload['task']}] [{index}/{total} {percentage:.1f}%] "
                f"item={payload['item']} stage={payload['stage']} status={status}"
            )
            return
        filled = min(20, max(0, int(index / total * 20)))
        bar = "█" * filled + "-" * (20 - filled)
        print(f"{payload['task']}  [{bar}]  {index}/{total}  {percentage:.1f}%")
        print(f"{payload['item']} · {payload['stage']}")
        print(
            f"success {self._counts['success']}  skipped {self._counts['skipped']}  "
            f"failed {self._counts['failed']}"
        )


def _main(arguments: Sequence[str] | None, *, legacy: bool) -> int:
    parsed = _parser(legacy=legacy).parse_args(arguments)
    default_task = "PRE_S1_SLC" if legacy else "PRE_GAMMA_SAR_TOPS_SLC"

    try:
        polarization, workflow_flag = _optional_arguments(parsed.optional)
        plan = build_slc_plan(
            parsed.RAW_dir,
            parsed.SLC_dir,
            parsed.OPOD_dir,
            parsed.images,
            parsed.ref_burst_number_table,
            parsed.rlks,
            parsed.azlks,
            polarization,
            workflow_flag,
        )
        recorded = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
        if recorded:
            sink = print
        else:
            sink = _LocalProgress(tty=sys.stdout.isatty())
        task = os.environ.get("GEODESY_TASK", default_task) if recorded else default_task
        run_slc_batch(plan, task=task, progress_sink=sink)
    except KeyboardInterrupt:
        print("ERROR: Sentinel-1 SLC generation interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, FileExistsError, OSError, ValueError, SlcCommandError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the backend-qualified GAMMA TOPS SLC workflow."""

    return _main(arguments, legacy=False)


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Run the historical S1-03 workflow with its original task identity."""

    return _main(arguments, legacy=True)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["legacy_main", "main"]
