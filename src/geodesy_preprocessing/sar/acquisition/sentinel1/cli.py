"""Sentinel-1 查询、下载和 BURST 合并的正式命令入口。

功能概述:
    从具名项目 CFG 执行互斥的查询、下载或合并；查询不读取凭据，下载和合并才验证
    Earthdata 配置，并同时支持直接进度与 Workflow 结构化事件。

函数说明:
    ``build_parser`` 构建命令参数；``main`` 提供已安装命令入口。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.sar.acquisition.earthdata import (
    earthdata_session_from_environment,
)

from .config import (
    load_sentinel1_acquisition_config,
    select_sentinel1_acquisition_case,
)
from .download import download_sentinel1
from .merge import merge_sentinel1_bursts
from .models import Sentinel1ProductType
from .query import load_query_for_download, publish_sentinel1_query
from .search import AsfSentinel1SearchClient


_TASK = "PRE_COMMON_SENTINEL1_ACQUISITION"
_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+):\s+([^:]+)$")


def build_parser() -> argparse.ArgumentParser:
    """Build the public parser with exactly one required action."""

    parser = argparse.ArgumentParser(
        prog="geodesy-pre-sar-sentinel1-acquire",
        description="Query, review, download, or merge Sentinel-1 BURST/SLC/GRD products.",
    )
    parser.add_argument("--config", required=True, type=Path, help="project CFG path")
    parser.add_argument("--case", help="named [case:name] to execute")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--query", action="store_true", help="query and publish review files")
    action.add_argument("--download", action="store_true", help="download the reviewed query")
    action.add_argument("--merge", action="store_true", help="merge reviewed BURST files to SAFE")
    return parser


def _redact(message: object) -> str:
    text = str(message)
    for name in ("EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD"):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "<redacted>")
    text = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://<redacted>@", text)
    text = re.sub(
        r"([?&](?:token|password|secret)=)[^&\s]+",
        r"\1<redacted>",
        text,
        flags=re.I,
    )
    return text


def _progress_sink(*, workflow: bool, stage: str) -> Callable[[str], None]:
    if not workflow:
        return print
    task = os.environ.get("GEODESY_TASK", _TASK)

    def emit(line: str) -> None:
        match = _PROGRESS.fullmatch(line.strip())
        if match is None:
            print(
                emit_progress(
                    event="message",
                    task=task,
                    stage=stage,
                    message=_redact(line),
                )
            )
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
                stage=stage,
                status="complete" if complete else "running",
            )
        )

    return emit


def main(argv: Sequence[str] | None = None) -> int:
    """Run one configured Sentinel-1 metadata query, download, or BURST merge."""

    options = build_parser().parse_args(argv)
    try:
        cases = load_sentinel1_acquisition_config(options.config)
        case = select_sentinel1_acquisition_case(cases, options.case)
        query = None
        if options.download or options.merge:
            query = load_query_for_download(case)
        if options.merge and case.product_type is not Sentinel1ProductType.BURST:
            raise ValueError("--merge is available only when product_type = BURST")
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"ERROR: {_redact(error)}", file=sys.stderr)
        return 2

    workflow = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
    try:
        if options.query:
            result = publish_sentinel1_query(case, AsfSentinel1SearchClient())
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
                        selected=len(result.selected_products),
                    )
                )
            else:
                print(
                    f"query complete: {len(result.all_products)} found, "
                    f"{len(result.selected_products)} selected; results={result.query_dir}"
                )
        elif options.download:
            session = earthdata_session_from_environment()
            download_sentinel1(
                case,
                query,
                session,
                progress_sink=_progress_sink(workflow=workflow, stage="download"),
            )
        else:
            earthdata_session_from_environment()
            merge_sentinel1_bursts(
                case,
                query,
                case.download_dir / "source_manifest.json",
                progress_sink=_progress_sink(workflow=workflow, stage="merge"),
            )
    except KeyboardInterrupt:
        print("ERROR: Sentinel-1 acquisition interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {_redact(error)}", file=sys.stderr)
        return 1
    return 0


__all__ = ["build_parser", "main"]
