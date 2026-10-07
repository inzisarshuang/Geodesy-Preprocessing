"""Backend-neutral path validation helpers.

功能概述:
    为公共获取层和各处理后端提供同一套项目根目录边界检查。

函数说明:
    ``validate_within_root`` 解析路径，并拒绝任何逃出项目根目录的结果。
"""

from __future__ import annotations

from pathlib import Path


def validate_within_root(path: Path, root: Path, label: str) -> Path:
    """Resolve ``path`` and reject a result outside the resolved ``root``."""

    resolved_root = root.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"{label} path is outside project root: {resolved}")
    return resolved


__all__ = ["validate_within_root"]
