"""GAMMA Sentinel-1 TOPS SLC batch planning and source discovery.

功能概述:
    校验 S1-03 输入，按影像列表顺序发现 SAFE/ZIP 来源，并生成不可变、只读的
    SLC 批处理计划。

函数说明:
    ``SlcItemPlan`` 描述单景来源和目标；``GammaTopsSlcPlan`` 描述完整批次；
    ``read_image_items`` 读取影像列表；``build_gamma_tops_slc_plan`` 创建确定性计划。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ITEM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_POLARIZATION_PATTERN = re.compile(r"^[a-z0-9]+$")


@dataclass(frozen=True)
class SlcItemPlan:
    """One ordered SLC item with resolved read-only sources."""

    item: str
    sources: tuple[Path, ...]
    output_dir: Path
    polarization: str
    skip: bool
    skip_reason: str | None


@dataclass(frozen=True)
class GammaTopsSlcPlan:
    """Validated inputs and ordered items for one S1-03 execution."""

    raw_dir: Path
    slc_dir: Path
    opod_dir: Path | None
    images_file: Path
    burst_table: Path
    range_looks: int
    azimuth_looks: int
    polarization_argument: str
    workflow: str
    items: tuple[SlcItemPlan, ...]


def read_image_items(path: str | Path) -> tuple[str, ...]:
    """Read unique safe item identifiers from the first non-empty column."""

    source = Path(path).expanduser().resolve(strict=True)
    items: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        item = fields[0]
        if item in {".", ".."} or not _ITEM_PATTERN.fullmatch(item):
            raise ValueError(f"unsafe image item at line {line_number}: {item!r}")
        if item in seen:
            raise ValueError(f"duplicate image item at line {line_number}: {item}")
        seen.add(item)
        items.append(item)
    if not items:
        raise ValueError(f"image list is empty: {source}")
    return tuple(items)


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _required_file(path: str | Path, name: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{name} must be a file: {resolved}")
    return resolved


def _required_directory(path: str | Path, name: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{name} must be a directory: {resolved}")
    return resolved


SlcBatchPlan = GammaTopsSlcPlan


def build_gamma_tops_slc_plan(
    raw_dir: str | Path,
    slc_dir: str | Path,
    opod_dir: str | Path,
    images_file: str | Path,
    burst_table: str | Path,
    range_looks: int,
    azimuth_looks: int,
    polarization: str = "-",
    workflow_flag: int = 0,
) -> GammaTopsSlcPlan:
    """Validate S1-03 inputs and return deterministic SAFE/ZIP work items."""

    raw = _required_directory(raw_dir, "RAW directory")
    output_root = Path(slc_dir).expanduser().resolve()
    images = _required_file(images_file, "images file")
    burst = _required_file(burst_table, "reference burst table")
    range_count = _positive_integer(range_looks, "range looks")
    azimuth_count = _positive_integer(azimuth_looks, "azimuth looks")
    if isinstance(workflow_flag, bool) or workflow_flag not in {0, 1}:
        raise ValueError("workflow_flag must be 0 (ZIP) or 1 (SAFE)")
    workflow = "safe" if workflow_flag == 1 else "zip"
    suffix = ".SAFE" if workflow == "safe" else ".zip"
    source_kind = "directory" if workflow == "safe" else "file"

    if str(opod_dir) == "-":
        opod: Path | None = None
    else:
        opod = _required_directory(opod_dir, "OPOD directory")

    argument = str(polarization).strip()
    normalized = "vv" if argument == "-" else argument.lower()
    if argument != "-" and not _POLARIZATION_PATTERN.fullmatch(normalized):
        raise ValueError("polarization must be '-' or a lowercase-compatible token")

    planned_items: list[SlcItemPlan] = []
    for item in read_image_items(images):
        candidates = sorted(raw.glob(f"*{item}*{suffix}"), key=lambda path: path.name)
        sources: list[Path] = []
        for candidate in candidates:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(raw):
                raise ValueError(f"source resolves outside RAW directory: {candidate}")
            correct_kind = resolved.is_dir() if workflow == "safe" else resolved.is_file()
            if correct_kind:
                sources.append(resolved)
        if not sources:
            raise FileNotFoundError(
                f"no {source_kind} source matching item {item} and suffix {suffix}"
            )
        item_output = output_root / item
        expected_mli = item_output / f"{item}.{normalized}.mli"
        skip = expected_mli.is_file()
        planned_items.append(
            SlcItemPlan(
                item=item,
                sources=tuple(sources),
                output_dir=item_output,
                polarization=normalized,
                skip=skip,
                skip_reason="requested MLI already exists" if skip else None,
            )
        )

    return GammaTopsSlcPlan(
        raw_dir=raw,
        slc_dir=output_root,
        opod_dir=opod,
        images_file=images,
        burst_table=burst,
        range_looks=range_count,
        azimuth_looks=azimuth_count,
        polarization_argument=argument,
        workflow=workflow,
        items=tuple(planned_items),
    )


build_slc_plan = build_gamma_tops_slc_plan


__all__ = [
    "GammaTopsSlcPlan",
    "SlcBatchPlan",
    "SlcItemPlan",
    "build_gamma_tops_slc_plan",
    "build_slc_plan",
    "read_image_items",
]
