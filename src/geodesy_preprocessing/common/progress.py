"""Shared language-neutral batch-progress producer.

功能概述:
    为各预处理模块生成确定性且不含凭据的 ``@@GEODESY_PROGRESS`` JSON 行。

函数说明:
    ``emit_progress`` 校验公共字段和附加信息并返回一条紧凑协议文本；
    ``ConsoleProgress`` 把该协议转换为供人阅读的统一终端进度。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

PROGRESS_PREFIX = "@@GEODESY_PROGRESS "
_SECRET_MARKERS = (
    "password",
    "secret",
    "token",
    "cookie",
    "api_key",
    "authorization",
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def emit_progress(
    *,
    event: str,
    task: str,
    timestamp: str | None = None,
    item: str | None = None,
    index: int | None = None,
    total: int | None = None,
    stage: str | None = None,
    status: str | None = None,
    **details: object,
) -> str:
    """Return one compact progress-protocol line without a trailing newline."""

    for key in details:
        normalized = key.lower().replace("-", "_")
        if any(marker in normalized for marker in _SECRET_MARKERS):
            raise ValueError(f"secret-like progress detail rejected: {key}")
    payload: dict[str, object] = {
        "event": event,
        "task": task,
        "timestamp": timestamp or _timestamp(),
    }
    optional = {
        "item": item,
        "index": index,
        "total": total,
        "stage": stage,
        "status": status,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    payload.update(details)
    return PROGRESS_PREFIX + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


class ConsoleProgress:
    """Render protocol events as concise, consistent human-readable lines."""

    def __init__(self, *, writer: Callable[[str], object] = print) -> None:
        self._writer = writer

    def __call__(self, line: str) -> None:
        if not line.startswith(PROGRESS_PREFIX):
            self._writer(line)
            return
        payload = json.loads(line.removeprefix(PROGRESS_PREFIX))
        task = payload.get("task", "GEODESY")
        item = payload.get("item", "-")
        stage = payload.get("stage", payload.get("event", "-"))
        status = payload.get("status", "-")
        index = payload.get("index")
        total = payload.get("total")
        if isinstance(index, int) and isinstance(total, int) and total > 0:
            position = f"[{index}/{total} {index / total * 100:.1f}%]"
        else:
            position = "[-/-]"
        self._writer(
            f"[{task}] {position} item={item} stage={stage} status={status}"
        )
        failure_logs = payload.get("failure_logs")
        if failure_logs:
            self._writer(f"详细日志: {failure_logs}")


__all__ = ["ConsoleProgress", "PROGRESS_PREFIX", "emit_progress"]
