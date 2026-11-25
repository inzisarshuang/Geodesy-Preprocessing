"""Transactional publication shared by preprocessing workflows.

功能概述:
    将已验证的同盘 staging 目录原子发布到正式路径；重跑时先保留旧结果，
    仅在新结果发布成功后删除备份，发布失败则恢复旧结果。若恢复失败，
    保留备份并在错误消息中报告其路径。

函数说明:
    ``atomic_replace_directory`` 发布一个完整目录，允许安全覆盖已有结果。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def atomic_replace_directory(staging: str | Path, output: str | Path) -> None:
    """Atomically replace ``output`` with a verified sibling staging directory."""

    source = Path(staging).expanduser()
    destination = Path(output).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"staging must be a real directory: {source}")
    if destination.is_symlink():
        raise ValueError(f"output directory must not be a symlink: {destination}")
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"output must be a directory: {destination}")
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination or source.parent != destination.parent:
        raise ValueError("staging and output must be distinct sibling directories")

    backup_root: Path | None = None
    previous: Path | None = None
    preserve_backup = False
    try:
        if destination.exists():
            backup_root = Path(
                tempfile.mkdtemp(
                    prefix=f".{destination.name}.",
                    suffix=".backup",
                    dir=destination.parent,
                )
            )
            previous = backup_root / "previous"
            os.replace(destination, previous)
        try:
            os.replace(source, destination)
        except BaseException:
            if previous is not None:
                try:
                    os.replace(previous, destination)
                except BaseException as rollback_error:
                    preserve_backup = True
                    raise RuntimeError(
                        f"could not restore {destination}; previous result retained at {previous}"
                    ) from rollback_error
            raise
    finally:
        if backup_root is not None and not preserve_backup:
            shutil.rmtree(backup_root, ignore_errors=True)


__all__ = ["atomic_replace_directory"]
