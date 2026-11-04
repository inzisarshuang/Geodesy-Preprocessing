"""Central publication and repair helpers for preprocessing quicklooks."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_IMAGE_SUFFIXES = {".bmp", ".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class FigureProduct:
    """One relative quicklook destination and its non-scientific render command."""

    relative_path: Path
    command: tuple[str, ...]


def centralize_pre_figures(science_root: Path, figure_dir: Path) -> tuple[Path, ...]:
    """Move image quicklooks out of a science tree into one central PRE directory."""

    source_root = Path(science_root).resolve(strict=True)
    destination_root = Path(figure_dir).expanduser().resolve()
    if destination_root.is_relative_to(source_root):
        raise ValueError("figure_dir must not be inside the science product directory")
    images = tuple(
        path
        for path in sorted(source_root.rglob("*"))
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    )
    published: list[Path] = []
    for source in images:
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            source.unlink()
        finally:
            temporary.unlink(missing_ok=True)
        published.append(destination)
    for directory in sorted(source_root.rglob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass
    return tuple(published)


def pre_figures_complete(paths: tuple[Path, ...]) -> bool:
    """Return whether every declared quicklook exists and is non-empty."""

    return bool(paths) and all(path.is_file() and path.stat().st_size > 0 for path in paths)


def required_pre_figures(manifest: Mapping[str, Any]) -> tuple[FigureProduct, ...]:
    """Parse a completion manifest's optional deterministic figure inventory."""

    raw = manifest.get("figures", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("figures must be a list")
    products: list[FigureProduct] = []
    for record in raw:
        if not isinstance(record, Mapping):
            raise ValueError("each figure record must be an object")
        relative = Path(str(record.get("path", "")))
        command = record.get("command")
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or not isinstance(command, Sequence)
            or isinstance(command, (str, bytes))
            or not command
            or any(not isinstance(value, str) or not value for value in command)
        ):
            raise ValueError("invalid figure path or command")
        products.append(FigureProduct(relative, tuple(command)))
    return tuple(products)


def repair_pre_figures(
    manifest: Mapping[str, Any],
    figure_dir: Path,
    command_runner: Callable[[tuple[str, ...], Path], None],
) -> tuple[Path, ...]:
    """Render only missing quicklooks, leaving scientific products untouched."""

    root = Path(figure_dir).expanduser().resolve()
    repaired: list[Path] = []
    for product in required_pre_figures(manifest):
        destination = root / product.relative_path
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        command_runner(product.command, destination)
        if not destination.is_file() or destination.stat().st_size < 1:
            raise ValueError(f"figure renderer did not create {destination}")
        repaired.append(destination)
    return tuple(repaired)


__all__ = [
    "FigureProduct",
    "centralize_pre_figures",
    "pre_figures_complete",
    "repair_pre_figures",
    "required_pre_figures",
]
