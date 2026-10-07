"""Atomic publication of legacy-compatible SAR date inventories.

功能概述:
    从已验证注册栈日期确定性生成一行一个 YYYYMMDD 的 images 文件。

函数说明:
    ``write_images_inventory`` 校验顺序和唯一性并原子发布日期表。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence


def _date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid acquisition date: {value!r}") from error
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"invalid acquisition date: {value!r}")
    return value


def write_images_inventory(path: Path, dates: Sequence[str]) -> Path:
    """Atomically write unique acquisition dates while preserving manifest order."""

    normalized = tuple(_date(value) for value in dates)
    if not normalized:
        raise ValueError("images inventory must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError("images inventory contains duplicate dates")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write("".join(f"{value}\n" for value in normalized))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


__all__ = ["write_images_inventory"]
