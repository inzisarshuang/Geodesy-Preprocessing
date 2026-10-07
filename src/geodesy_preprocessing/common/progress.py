"""Shared language-neutral batch-progress producer.

功能概述:
    为各预处理模块生成确定性且不含凭据的 ``@@GEODESY_PROGRESS`` JSON 行。

函数说明:
    ``emit_progress`` 校验公共字段和附加信息并返回一条紧凑协议文本。
"""

from __future__ import annotations

import json
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


__all__ = ["PROGRESS_PREFIX", "emit_progress"]
