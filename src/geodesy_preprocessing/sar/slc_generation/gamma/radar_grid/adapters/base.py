"""Adapter contract and safe archive handling for GAMMA vendor products.

功能概述:
    定义供应商适配器共同接口，并在解压前完整检查 ZIP 成员，阻止路径逃逸和特殊文件。

函数说明:
    ``PreparedProduct`` 保存暂存后的输入；``SensorAdapter`` 定义适配器协议；
    ``safe_extract_zip`` 验证并解压普通文件和目录。
"""

from __future__ import annotations

import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from ..config import OtherSarSlcCase
from ..models import ScenePlan, SourceProduct


@dataclass(frozen=True)
class PreparedProduct:
    """One vendor acquisition safely materialized inside scene staging."""

    product: SourceProduct
    working_root: Path
    leader: Path
    images: tuple[tuple[str, Path], ...]


class SensorAdapter(Protocol):
    """Common discovery, preparation, and planning contract."""

    sensor: str

    def discover(self, case: OtherSarSlcCase) -> tuple[SourceProduct, ...]: ...

    def prepare(self, product: SourceProduct, staging_dir: Path) -> PreparedProduct: ...

    def build_scene_plan(
        self, case: OtherSarSlcCase, prepared: PreparedProduct
    ) -> ScenePlan: ...

    def finalize_scene(self, prepared: PreparedProduct, plan: ScenePlan) -> None: ...


def _validated_members(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, ...]:
    members = archive.infolist()
    seen: set[str] = set()
    for member in members:
        name = member.filename
        if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise ValueError(f"unsafe ZIP member path: {name!r}")
        parts = PurePosixPath(name).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"unsafe ZIP member path: {name!r}")
        canonical = "/".join(parts)
        if canonical in seen:
            raise ValueError(f"duplicate ZIP member: {canonical}")
        seen.add(canonical)
        mode = (member.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ValueError(f"special ZIP member is not allowed: {name!r}")
    return tuple(members)


def safe_extract_zip(archive_path: Path, destination: Path) -> tuple[Path, ...]:
    """Extract a fully validated ZIP into a new staging-only directory."""

    archive_source = archive_path.resolve(strict=True)
    with zipfile.ZipFile(archive_source) as archive:
        members = _validated_members(archive)
        destination.mkdir(parents=True, exist_ok=False)
        destination_root = destination.resolve(strict=True)
        extracted: list[Path] = []
        for member in members:
            relative = Path(*PurePosixPath(member.filename).parts)
            target = (destination_root / relative).resolve()
            if not target.is_relative_to(destination_root):
                raise ValueError(f"unsafe ZIP member escape: {member.filename!r}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(target)
    return tuple(extracted)


__all__ = ["PreparedProduct", "SensorAdapter", "safe_extract_zip"]
