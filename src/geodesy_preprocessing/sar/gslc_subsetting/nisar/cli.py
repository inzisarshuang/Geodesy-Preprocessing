"""NISAR GSLC 原生复数栈的 plan-only 与正式执行命令。

功能概述:
    从具名项目 CFG 构建只读计划；可只发布计划，也可事务生成完整地理网格复数栈。
    直接运行显示逐景进度，Workflow 模式原样输出结构化进度事件。

函数说明:
    ``main`` 提供已安装的 ``geodesy-pre-sar-nisar-gslc`` 命令入口。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from geodesy_preprocessing.common.progress import PROGRESS_PREFIX, emit_progress

from .config import load_nisar_gslc_stack_config, select_nisar_gslc_stack_case
from .plan import build_nisar_gslc_stack_plan
from .publication import publish_gslc_plan
from .runner import _redact, run_nisar_gslc_stack


_TASK = "PRE_NISAR_GSLC"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodesy-pre-sar-nisar-gslc",
        description="Plan or publish a native NISAR GSLC geocoded complex stack.",
    )
    parser.add_argument("--config", required=True, type=Path, help="project CFG path")
    parser.add_argument("--case", help="named [case:name] to execute")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="validate inputs and publish a plan under LOG without extracting rasters",
    )
    return parser


def _progress_sink(*, workflow: bool):
    if workflow:
        return print

    def display(line: str) -> None:
        if not line.startswith(PROGRESS_PREFIX):
            print(line)
            return
        try:
            event = json.loads(line.removeprefix(PROGRESS_PREFIX))
        except json.JSONDecodeError:
            print(line)
            return
        if event.get("item") is not None:
            print(
                f"[{event.get('index', '-')}/{event.get('total', '-')}] "
                f"{event['item']}: {event.get('status', event.get('event'))}"
            )
        elif event.get("event") == "batch_finished":
            print(f"batch: {event.get('status', 'finished')}")

    return display


def main(arguments: Sequence[str] | None = None) -> int:
    """Plan or execute one configured native NISAR GSLC stack case."""

    options = _parser().parse_args(arguments)
    try:
        cases = load_nisar_gslc_stack_config(options.config)
        case = select_nisar_gslc_stack_case(cases, options.case)
        plan = build_nisar_gslc_stack_plan(case)
    except KeyboardInterrupt:
        print("ERROR: NISAR GSLC planning interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, FileExistsError, OSError, TypeError, ValueError) as error:
        print(f"ERROR: {_redact(error)}", file=sys.stderr)
        return 2

    workflow = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
    try:
        if options.plan_only:
            path = publish_gslc_plan(plan)
            if workflow:
                print(
                    emit_progress(
                        event="batch_finished",
                        task=os.environ.get("GEODESY_TASK", _TASK),
                        item=case.name,
                        status="planned",
                        total=len(plan.assets),
                        stage="plan",
                    )
                )
            else:
                print(f"plan complete: {path}")
            return 0
        summary = run_nisar_gslc_stack(
            plan,
            progress_sink=_progress_sink(workflow=workflow),
        )
        if not workflow:
            print(
                f"stack complete: completed={summary.completed}, skipped={summary.skipped}; "
                f"output={summary.output_dir}"
            )
    except KeyboardInterrupt:
        print("ERROR: NISAR GSLC execution interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, FileExistsError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"ERROR: {_redact(error)}", file=sys.stderr)
        return 1
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
