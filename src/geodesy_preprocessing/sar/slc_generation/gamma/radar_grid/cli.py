"""Project-configured command line for GAMMA radar-grid SLC generation.

功能概述:
    从具名项目配置选择一个卫星 case，支持只读检查，并在直接运行时显示简洁统一进度，
    在 Workflow 模式下输出原始结构化进度行。

函数说明:
    ``main`` 提供 GAMMA 正式命令；``legacy_main`` 保留 Other-SAR 命令及任务名。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from geodesy_preprocessing.common.progress import PROGRESS_PREFIX

from .config import (
    load_gamma_radar_slc_config,
    load_other_sar_slc_config,
    select_gamma_radar_slc_case,
    select_other_sar_slc_case,
)
from .plan import build_slc_plan
from .runner import OtherSarCommandError, run_slc_batch


def _parser(*, legacy: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodesy-pre-sar-slc" if legacy else "geodesy-pre-gamma-sar-slc",
        description="Convert supported radar-grid vendor products to GAMMA SLC.",
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--case")
    parser.add_argument("--dry-run", action="store_true")
    return parser


class _LocalProgress:
    def __init__(self, *, tty: bool) -> None:
        self._tty = tty
        self._counts = {"complete": 0, "skipped": 0, "failed": 0}

    def __call__(self, line: str) -> None:
        payload = json.loads(line[len(PROGRESS_PREFIX) :])
        if payload["event"] != "item_finished":
            return
        status = str(payload["status"])
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
            f"complete {self._counts['complete']}  skipped {self._counts['skipped']}  "
            f"failed {self._counts['failed']}"
        )


def _main(arguments: Sequence[str] | None, *, legacy: bool) -> int:
    options = _parser(legacy=legacy).parse_args(arguments)
    loader = load_other_sar_slc_config if legacy else load_gamma_radar_slc_config
    selector = select_other_sar_slc_case if legacy else select_gamma_radar_slc_case

    try:
        cases = loader(options.config)
        case = selector(cases, options.case)
        plan = build_slc_plan(case)
        if options.dry_run:
            print(
                f"case={case.name} sensor={case.sensor} scenes={len(plan.products)} "
                f"polarizations={','.join(case.polarizations)}"
            )
            for product in plan.products:
                print(f"{product.date} source={product.root}")
            return 0
        recorded = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
        if recorded:
            task = os.environ.get("GEODESY_TASK", case.workflow_task)
            if task != case.workflow_task:
                case = replace(case, workflow_task=task)
                plan = build_slc_plan(case)
            sink = print
        else:
            sink = _LocalProgress(tty=sys.stdout.isatty())
        run_slc_batch(plan, progress_sink=sink)
    except KeyboardInterrupt:
        print("ERROR: Other-SAR SLC generation interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except OtherSarCommandError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate, describe, or execute one GAMMA radar-grid SLC case."""

    return _main(arguments, legacy=False)


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Execute the historical Other-SAR command with its original default task."""

    return _main(arguments, legacy=True)


__all__ = ["legacy_main", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
