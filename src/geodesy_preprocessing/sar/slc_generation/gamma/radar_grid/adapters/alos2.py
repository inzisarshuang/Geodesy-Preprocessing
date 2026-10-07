"""GAMMA ALOS-2/PALSAR CEOS Level 1.1 adapter.

功能概述:
    只读检查 CEOS ZIP，解析 summary/workreport 日期及极化文件，在本景暂存目录中安全解压，
    并生成保持旧目录布局的 ``par_EORC_PALSAR`` 计划。

函数说明:
    ``ALOS2Adapter`` 实现产品发现、暂存准备和一景命令计划。
"""

from __future__ import annotations

import re
import shlex
import zipfile
from pathlib import Path, PurePosixPath

from ..config import OtherSarSlcCase, validate_safe_date, validate_within_root
from ..models import CommandSpec, ProductOutput, ScenePlan, SourceProduct
from ..publication import classify_output_group
from .base import PreparedProduct, _validated_members, safe_extract_zip

_DATE_PATTERN = re.compile(r"Img_SceneCenterDateTime[^\r\n\d]*(\d{8})")
_IMAGE_PATTERN = re.compile(r"^IMG-(HH|HV|VH|VV)(?:-|$)", re.IGNORECASE)


def _member_basename(name: str) -> str:
    return PurePosixPath(name).name


def _inspect_archive(archive_path: Path) -> tuple[str, tuple[str, ...]]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _validated_members(archive)
            metadata = [
                member
                for member in members
                if _member_basename(member.filename) in {"summary.txt", "workreport"}
            ]
            if len(metadata) != 1:
                raise ValueError(
                    f"{archive_path}: expected exactly one summary.txt or workreport"
                )
            text = archive.read(metadata[0]).decode("utf-8", errors="strict")
            match = _DATE_PATTERN.search(text)
            if match is None:
                raise ValueError(f"{archive_path}: acquisition date is missing from metadata")
            date = validate_safe_date(match.group(1))
            leaders = [
                member
                for member in members
                if _member_basename(member.filename).startswith("LED") and not member.is_dir()
            ]
            if len(leaders) != 1:
                raise ValueError(f"{archive_path}: expected exactly one leader, found {len(leaders)}")
            polarizations: list[str] = []
            counts: dict[str, int] = {}
            for member in members:
                match = _IMAGE_PATTERN.match(_member_basename(member.filename))
                if match is None or member.is_dir():
                    continue
                polarization = match.group(1).upper()
                counts[polarization] = counts.get(polarization, 0) + 1
                if polarization not in polarizations:
                    polarizations.append(polarization)
            ambiguous = [key for key, count in counts.items() if count != 1]
            if ambiguous:
                raise ValueError(f"{archive_path}: ambiguous IMG file for {ambiguous[0]}")
            if not polarizations:
                raise ValueError(f"{archive_path}: no IMG polarization files found")
            return date, tuple(sorted(polarizations))
    except zipfile.BadZipFile as error:
        raise ValueError(f"{archive_path}: invalid ZIP archive") from error


class ALOS2Adapter:
    """Adapt read-only ALOS/PALSAR CEOS archives to GAMMA SLC plans."""

    sensor = "alos2"

    def discover(self, case: OtherSarSlcCase) -> tuple[SourceProduct, ...]:
        """Discover unique dated archives and validate requested polarizations."""

        raw_root = case.raw_dir.resolve(strict=True)
        archives = sorted(
            path
            for path in raw_root.rglob("*.zip")
            if path.is_file() and not path.is_symlink()
        )
        if not archives:
            raise ValueError(f"{raw_root}: no ALOS/PALSAR ZIP archives found")
        products: list[SourceProduct] = []
        seen_dates: set[str] = set()
        for archive in archives:
            resolved = validate_within_root(archive, raw_root, "ALOS archive")
            date, available = _inspect_archive(resolved)
            if date in seen_dates:
                raise ValueError(f"duplicate acquisition date {date} in {raw_root}")
            seen_dates.add(date)
            for polarization in case.polarizations:
                if polarization not in available:
                    raise ValueError(
                        f"{resolved}: requested polarization {polarization} is absent"
                    )
            products.append(
                SourceProduct(
                    sensor=self.sensor,
                    date=date,
                    root=resolved,
                    source_files=(resolved,),
                    polarizations=available,
                )
            )
        return tuple(sorted(products, key=lambda product: product.date))

    def prepare(self, product: SourceProduct, staging_dir: Path) -> PreparedProduct:
        """Extract one validated archive inside its isolated staging directory."""

        vendor_root = staging_dir / "vendor"
        extracted = safe_extract_zip(product.root, vendor_root)
        leaders = [path for path in extracted if path.name.startswith("LED")]
        if len(leaders) != 1:
            raise ValueError(f"{product.root}: expected exactly one leader after extraction")
        images: list[tuple[str, Path]] = []
        for path in extracted:
            match = _IMAGE_PATTERN.match(path.name)
            if match is not None:
                images.append((match.group(1).upper(), path))
        return PreparedProduct(
            product=product,
            working_root=vendor_root,
            leader=leaders[0],
            images=tuple(sorted(images)),
        )

    def build_scene_plan(
        self, case: OtherSarSlcCase, prepared: PreparedProduct
    ) -> ScenePlan:
        """Build literal import commands and legacy per-date destinations."""

        product = prepared.product
        image_by_pol = dict(prepared.images)
        command_prefix = tuple(
            shlex.split(case.commands.get("import_command", "par_EORC_PALSAR"))
        )
        staging_dir = prepared.working_root.parent
        commands: list[CommandSpec] = []
        outputs: list[ProductOutput] = []
        selected_polarizations = case.polarizations or product.polarizations
        for polarization in selected_polarizations:
            output_relative_root = Path("products") / polarization / product.date
            slc_relative = output_relative_root / f"{product.date}.slc"
            par_relative = output_relative_root / f"{product.date}.slc.par"
            slc_path = staging_dir / slc_relative
            par_path = staging_dir / par_relative
            commands.append(
                CommandSpec(
                    stage=f"import_{polarization.lower()}",
                    argv=command_prefix
                    + (
                        str(prepared.leader),
                        str(par_path),
                        str(image_by_pol[polarization]),
                        str(slc_path),
                    ),
                )
            )
            destination_root = case.slc_dir / f"{polarization}_SLC" / product.date
            outputs.extend(
                (
                    ProductOutput(
                        polarization,
                        slc_relative,
                        destination_root / f"{product.date}.slc",
                    ),
                    ProductOutput(
                        polarization,
                        par_relative,
                        destination_root / f"{product.date}.slc.par",
                    ),
                )
            )
        state = "skip" if classify_output_group(outputs) == "complete" else "run"
        return ScenePlan(product, tuple(commands), tuple(outputs), state)

    def finalize_scene(self, prepared: PreparedProduct, plan: ScenePlan) -> None:
        """Leave ALOS outputs unchanged because commands write fixed staged paths."""

        return None


__all__ = ["ALOS2Adapter"]
