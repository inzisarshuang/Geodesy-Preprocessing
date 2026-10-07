"""Transactional publication for GAMMA radar-grid scene products.

功能概述:
    在全部暂存文件通过验证后成组发布，失败时撤销本景已完成的重命名，并以原子方式写影像日期表。

函数说明:
    ``classify_output_group`` 区分缺失、完整和残缺结果；``publish_output_group``
    发布一组文件；``publish_images_table`` 原子写入排序去重后的日期。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .config import validate_safe_date
from .models import ProductOutput


def _replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def classify_output_group(outputs: Sequence[ProductOutput]) -> str:
    """Return ``absent`` or ``complete`` and reject partial/unsafe groups."""

    if not outputs:
        raise ValueError("output group must not be empty")
    present: list[bool] = []
    for output in outputs:
        destination = output.destination
        if destination.is_symlink():
            raise ValueError(f"output destination is a symlink: {destination}")
        exists = destination.exists()
        present.append(exists)
        if exists and (not destination.is_file() or destination.stat().st_size < 1):
            raise ValueError(f"incomplete output group contains invalid file: {destination}")
    if all(present):
        return "complete"
    if any(present):
        raise ValueError("incomplete output group; repair or move it before retrying")
    return "absent"


def publish_output_group(
    staging_dir: Path, outputs: Sequence[ProductOutput]
) -> None:
    """Validate and publish all staged outputs, rolling back on failure."""

    staging_root = staging_dir.resolve(strict=True)
    if classify_output_group(outputs) != "absent":
        raise ValueError("complete output group must be skipped before publication")
    staged: list[tuple[Path, Path]] = []
    for output in outputs:
        candidate = staging_root / output.staged_relative
        if candidate.is_symlink():
            raise ValueError(f"staged output is a symlink: {candidate}")
        try:
            source = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError(
                f"required staged output is missing: {output.staged_relative}"
            ) from error
        if not source.is_relative_to(staging_root):
            raise ValueError(f"staged output escapes staging directory: {source}")
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"staged output is not a regular file: {source}")
        if source.stat().st_size < 1:
            raise ValueError(f"staged output is empty: {source}")
        destination = output.destination
        if destination.is_symlink():
            raise ValueError(f"output destination is a symlink: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_dev != destination.parent.stat().st_dev:
            raise OSError(f"staged output and destination are on different filesystems: {destination}")
        staged.append((source, destination))

    published: list[tuple[Path, Path]] = []
    try:
        for source, destination in staged:
            with source.open("rb") as stream:
                os.fsync(stream.fileno())
            _replace_path(source, destination)
            published.append((source, destination))
            _fsync_directory(destination.parent)
    except BaseException:
        for source, destination in reversed(published):
            if destination.exists() and not source.exists():
                _replace_path(destination, source)
        raise


def publish_images_table(path: Path, dates: Sequence[str]) -> None:
    """Atomically publish unique sorted acquisition dates, one per line."""

    normalized = sorted({validate_safe_date(value) for value in dates})
    if not normalized:
        raise ValueError("images table must contain at least one acquisition date")
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        raise ValueError(f"images table destination is a symlink: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("".join(f"{value}\n" for value in normalized))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["classify_output_group", "publish_images_table", "publish_output_group"]
