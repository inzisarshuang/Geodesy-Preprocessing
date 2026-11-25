"""GAMMA SAOCOM single- and dual-polarization adapter.

功能概述:
    在每景 ``Data`` 目录中按共同文件干配对栅格与 XML，以确定顺序映射到 HH 或 HH/HV，
    并明确拒绝旧脚本未完成的全极化分支。

函数说明:
    ``saocom_polarizations_for_mode`` 翻译旧模式；``SaocomAdapter`` 实现产品发现、
    暂存描述和 ``par_SAOCOM_SLC`` 命令计划。
"""

from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path

from ..config import OtherSarSlcCase, validate_safe_date, validate_within_root
from ..models import CommandSpec, ProductOutput, ScenePlan, SourceProduct
from .base import PreparedProduct

_DATE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_SENSE_DATE = re.compile(r"^\s*(\d{4})-([A-Za-z]{3})-(\d{1,2})\s*$")
_LINES_START = re.compile(r"^\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})(?:\s|$)")
_MONTH = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
_SUPPORTED = {("HH",), ("HH", "HV")}
_INCOMPLETE_MESSAGE = "SAOCOM mode is unsupported because the historical branch was incomplete"


def saocom_polarizations_for_mode(mode: str) -> tuple[str, ...]:
    """Translate supported legacy modes and reject the unfinished full-pol mode."""

    normalized = mode.strip().lower()
    if normalized == "aa":
        return ("HH",)
    if normalized == "aa-ab":
        return ("HH", "HV")
    raise ValueError(_INCOMPLETE_MESSAGE)


def _metadata_date(xml: Path) -> str | None:
    """Read the acquisition date stored in a SAOCOM product XML."""

    candidates: set[str] = set()
    root = ET.parse(xml).getroot()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        value = (element.text or "").strip()
        if tag == "SenseDate":
            match = _SENSE_DATE.match(value)
            if match:
                year, month_name, day = match.groups()
                month = _MONTH.get(month_name.upper())
                if month is not None:
                    candidates.add(f"{int(year):04d}{month:02d}{int(day):02d}")
        elif tag == "LinesStart":
            match = _LINES_START.match(value)
            if match:
                day, month_name, year = match.groups()
                month = _MONTH.get(month_name.upper())
                if month is not None:
                    candidates.add(f"{int(year):04d}{month:02d}{int(day):02d}")
    valid = {validate_safe_date(candidate) for candidate in candidates}
    if len(valid) > 1:
        raise ValueError(f"{xml}: inconsistent acquisition dates in metadata")
    return next(iter(valid), None)


def _scene_date(scene: Path, xml_files: tuple[Path, ...]) -> str:
    metadata_dates = {
        date for xml in xml_files if (date := _metadata_date(xml)) is not None
    }
    if len(metadata_dates) > 1:
        raise ValueError(f"{scene}: inconsistent acquisition dates across product XML files")
    if metadata_dates:
        return next(iter(metadata_dates))

    # Older fixtures and some repackaged products expose only an eight-digit date.
    candidates = set(_DATE.findall(scene.name))
    for xml in xml_files:
        candidates.update(_DATE.findall(xml.read_text(encoding="utf-8")))
    valid: set[str] = set()
    for candidate in candidates:
        valid.add(validate_safe_date(candidate))
    if len(valid) != 1:
        raise ValueError(f"{scene}: expected exactly one valid acquisition date")
    return next(iter(valid))


class SaocomAdapter:
    """Adapt the two historically complete SAOCOM product modes."""

    sensor = "saocom"

    def discover(self, case: OtherSarSlcCase) -> tuple[SourceProduct, ...]:
        """Discover ordered raster/XML pairs and unique acquisition dates."""

        if case.polarizations not in _SUPPORTED:
            raise ValueError(_INCOMPLETE_MESSAGE)
        raw_root = case.raw_dir.resolve(strict=True)
        scenes = sorted(path for path in raw_root.iterdir() if path.is_dir())
        if not scenes:
            raise ValueError(f"{raw_root}: no SAOCOM acquisition directories found")
        products: list[SourceProduct] = []
        seen_dates: set[str] = set()
        for scene in scenes:
            data_dir = scene / "Data"
            if not data_dir.is_dir() or data_dir.is_symlink():
                raise ValueError(f"{scene}: required Data directory is missing")
            xml_files = tuple(
                sorted(
                    path
                    for path in data_dir.iterdir()
                    if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".xml"
                )
            )
            pairs: list[tuple[Path, Path]] = []
            for xml in xml_files:
                stem = xml.stem
                rasters = sorted(
                    path
                    for path in data_dir.iterdir()
                    if path.is_file()
                    and not path.is_symlink()
                    and path.suffix.lower() != ".xml"
                    and path.stem == stem
                )
                if not rasters:
                    raise ValueError(f"{xml}: matching raster is missing")
                if len(rasters) != 1:
                    raise ValueError(f"{xml}: expected exactly one raster, found {len(rasters)}")
                pairs.append((rasters[0], xml))
            if len(pairs) != len(case.polarizations):
                raise ValueError(
                    f"{data_dir}: expected {len(case.polarizations)} raster/XML pairs, found {len(pairs)}"
                )
            date = _scene_date(scene, xml_files)
            if date in seen_dates:
                raise ValueError(f"duplicate acquisition date {date} in {raw_root}")
            seen_dates.add(date)
            sources = tuple(
                validate_within_root(path, raw_root, "SAOCOM source")
                for pair in pairs
                for path in pair
            )
            products.append(
                SourceProduct(
                    sensor=self.sensor,
                    date=date,
                    root=scene.resolve(),
                    source_files=sources,
                    polarizations=case.polarizations,
                )
            )
        return tuple(sorted(products, key=lambda product: product.date))

    def prepare(self, product: SourceProduct, staging_dir: Path) -> PreparedProduct:
        """Assign each ordered source pair to its logical polarization."""

        if len(product.source_files) != 2 * len(product.polarizations):
            raise ValueError(f"{product.root}: incomplete SAOCOM source pairs")
        roles: list[tuple[str, Path]] = []
        for index, polarization in enumerate(product.polarizations):
            roles.append((f"{polarization}_DATA", product.source_files[2 * index]))
            roles.append((f"{polarization}_XML", product.source_files[2 * index + 1]))
        return PreparedProduct(
            product=product,
            working_root=staging_dir,
            leader=product.source_files[0],
            images=tuple(roles),
        )

    def build_scene_plan(
        self, case: OtherSarSlcCase, prepared: PreparedProduct
    ) -> ScenePlan:
        """Build one direct-output import command per logical polarization."""

        prefix = tuple(
            shlex.split(case.commands.get("import_command", "par_SAOCOM_SLC"))
        )
        roles = dict(prepared.images)
        date = prepared.product.date
        commands: list[CommandSpec] = []
        outputs: list[ProductOutput] = []
        for polarization in case.polarizations:
            relative_root = Path("products") / polarization
            (prepared.working_root / relative_root).mkdir(parents=True, exist_ok=True)
            slc_relative = relative_root / f"{date}.slc"
            par_relative = relative_root / f"{date}.slc.par"
            slc = prepared.working_root / slc_relative
            par = prepared.working_root / par_relative
            commands.append(
                CommandSpec(
                    f"import_{polarization.lower()}",
                    prefix
                    + (
                        str(roles[f"{polarization}_DATA"]),
                        str(roles[f"{polarization}_XML"]),
                        str(par),
                        str(slc),
                        "-",
                    ),
                )
            )
            destination_root = case.slc_dir / f"{polarization}_SLC"
            outputs.extend(
                (
                    ProductOutput(polarization, slc_relative, destination_root / slc.name),
                    ProductOutput(polarization, par_relative, destination_root / par.name),
                )
            )
        return ScenePlan(prepared.product, tuple(commands), tuple(outputs), "run")

    def finalize_scene(self, prepared: PreparedProduct, plan: ScenePlan) -> None:
        """Leave SAOCOM products unchanged because staged names are fixed."""

        return None


__all__ = ["SaocomAdapter", "saocom_polarizations_for_mode"]
