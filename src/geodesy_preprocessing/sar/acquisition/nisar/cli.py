"""NISAR RSLC/GSLC 查询、人工筛选后下载的公共命令入口。

功能概述:
    从具名项目 CFG 执行互斥的查询或下载；查询不接触凭据，下载才从环境或
    ``.netrc`` 建立 Earthdata 会话，并支持直接进度与 Workflow 结构化事件。

函数说明:
    ``main`` 提供 ``geodesy-pre-sar-nisar-acquire`` 正式入口。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.sar.acquisition.earthdata import (
    earthdata_session_from_environment,
)

from .config import load_nisar_config, select_nisar_case
from .download import download_nisar
from .query import load_query_for_download, run_nisar_query
from .search import AsfSearchClient


_TASK = "PRE_COMMON_NISAR_ACQUISITION"
_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]\s+([^:]+):\s+(.+)$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodesy-pre-sar-nisar-acquire",
        description="Query NISAR RSLC or GSLC coverage, or download a reviewed query.",
    )
    parser.add_argument("--config", required=True, type=Path, help="project CFG path")
    parser.add_argument("--case", help="named [case:name] to execute")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--query", action="store_true", help="query and publish review files")
    action.add_argument("--download", action="store_true", help="download the reviewed query")
    return parser


def _earthdata_session():
    return earthdata_session_from_environment()


def _progress_sink(*, workflow: bool):
    if not workflow:
        return print
    task = os.environ.get("GEODESY_TASK", _TASK)

    def emit(line: str) -> None:
        match = _PROGRESS.fullmatch(line.strip())
        if match is None:
            print(emit_progress(event="message", task=task, stage="download", message=line))
            return
        index, total, item, status = match.groups()
        complete = status == "complete"
        print(
            emit_progress(
                event="item_finished" if complete else "item_started",
                task=task,
                item=item,
                index=int(index),
                total=int(total),
                stage="download",
                status="complete" if complete else "running",
            )
        )

    return emit


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one configured NISAR metadata query or reviewed download."""

    options = _parser().parse_args(arguments)
    try:
        cases = load_nisar_config(options.config)
        case = select_nisar_case(cases, options.case)
        query = None
        if options.download:
            query = load_query_for_download(case)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    workflow = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
    try:
        if options.query:
            result = run_nisar_query(case, AsfSearchClient())
            if workflow:
                print(
                    emit_progress(
                        event="item_finished",
                        task=os.environ.get("GEODESY_TASK", _TASK),
                        item=case.name,
                        index=1,
                        total=1,
                        stage="query",
                        status="complete",
                        selected=len(result.selected_granules),
                    )
                )
            else:
                print(
                    f"query complete: {len(result.all_granules)} found, "
                    f"{len(result.selected_granules)} selected; results={result.query_dir}"
                )
        else:
            session = _earthdata_session()
            download_nisar(
                case,
                query,
                session,
                progress_sink=_progress_sink(workflow=workflow),
            )
    except KeyboardInterrupt:
        print("ERROR: NISAR acquisition interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
