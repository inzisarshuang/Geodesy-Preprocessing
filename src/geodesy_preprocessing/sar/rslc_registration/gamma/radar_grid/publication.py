"""GAMMA 普通雷达坐标 RSLC 栈目标目录状态检查。

功能概述:
    在执行任何 GAMMA 命令前区分不存在、完整可验证和残缺危险的目标目录。

函数说明:
    ``classify_stack_destination`` 返回 ``absent`` 或 ``complete``，残缺目录直接报错。
"""

from __future__ import annotations

from pathlib import Path

from geodesy_preprocessing.sar.common.stack_manifest import read_stack_manifest


def classify_stack_destination(path: str | Path) -> str:
    """Classify a stack destination and reject every non-manifest partial result."""

    destination = Path(path)
    if not destination.exists():
        return "absent"
    if destination.is_symlink() or not destination.is_dir():
        raise FileExistsError(f"RSLC destination is not a safe directory: {destination}")
    manifest = destination / "registered_stack.json"
    if manifest.is_file() and manifest.stat().st_size > 0:
        try:
            read_stack_manifest(manifest)
        except (FileNotFoundError, OSError, TypeError, ValueError) as error:
            raise FileExistsError(
                f"incomplete RSLC destination has an invalid manifest: {destination}"
            ) from error
        return "complete"
    raise FileExistsError(
        f"incomplete RSLC destination exists and will not be removed: {destination}"
    )


__all__ = ["classify_stack_destination"]
