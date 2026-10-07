"""GAMMA RADARSAT-2 XML/TIFF product adapter.

功能概述:
    从产品 XML 解析采集日期，严格配对校准表和所需极化影像，并为所有极化生成同景成组发布计划。

函数说明:
    ``Rsat2Adapter`` 实现 RADARSAT-2 产品发现、暂存描述和 ``par_RSAT2_SLC`` 计划。
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..config import OtherSarSlcCase, validate_safe_date, validate_within_root
from ..models import CommandSpec, ProductOutput, ScenePlan, SourceProduct
from ..publication import classify_output_group
from .base import PreparedProduct

_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})T")


def _one_named(scene: Path, name: str) -> Path:
    matches = sorted(
        path
        for path in scene.rglob(name)
        if path.is_file() and not path.is_symlink() and path.name == name
    )
    if len(matches) != 1:
        raise ValueError(f"{scene}: expected exactly one {name}, found {len(matches)}")
    return matches[0]


def _metadata_date(product_xml: Path) -> str:
    try:
        text = product_xml.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{product_xml}: product.xml is not UTF-8") from error
    matches = _ISO_DATE.findall(text)
    if len(matches) != 1:
        raise ValueError(f"{product_xml}: expected one valid acquisition date")
    year, month, day = matches[0]
    try:
        return validate_safe_date(f"{year}{month}{day}")
    except ValueError as error:
        raise ValueError(f"{product_xml}: expected one valid acquisition date") from error


class Rsat2Adapter:
    """Adapt RADARSAT-2 products without the historical HH-only restriction."""

    sensor = "rsat2"

    def discover(self, case: OtherSarSlcCase) -> tuple[SourceProduct, ...]:
        """Discover complete requested-polarization products with unique dates."""

        raw_root = case.raw_dir.resolve(strict=True)
        scenes = sorted(path for path in raw_root.iterdir() if path.is_dir())
        if not scenes:
            raise ValueError(f"{raw_root}: no RADARSAT-2 acquisition directories found")
        products: list[SourceProduct] = []
        seen_dates: set[str] = set()
        for scene in scenes:
            product_xml = validate_within_root(
                _one_named(scene, "product.xml"), raw_root, "RADARSAT-2 product XML"
            )
            lut = validate_within_root(
                _one_named(scene, "lutSigma.xml"), raw_root, "RADARSAT-2 LUT"
            )
            images = tuple(
                validate_within_root(
                    _one_named(scene, f"imagery_{polarization}.tif"),
                    raw_root,
                    f"RADARSAT-2 {polarization} image",
                )
                for polarization in case.polarizations
            )
            date = _metadata_date(product_xml)
            if date in seen_dates:
                raise ValueError(f"duplicate acquisition date {date} in {raw_root}")
            seen_dates.add(date)
            products.append(
                SourceProduct(
                    sensor=self.sensor,
                    date=date,
                    root=scene.resolve(),
                    source_files=(product_xml, lut) + images,
                    polarizations=case.polarizations,
                )
            )
        return tuple(sorted(products, key=lambda product: product.date))

    def prepare(self, product: SourceProduct, staging_dir: Path) -> PreparedProduct:
        """Describe product XML, LUT, and polarization images without copying sources."""

        if len(product.source_files) != len(product.polarizations) + 2:
            raise ValueError(f"{product.root}: incomplete RADARSAT-2 source description")
        product_xml, lut, *images = product.source_files
        roles = (("LUT", lut),) + tuple(zip(product.polarizations, images, strict=True))
        return PreparedProduct(
            product=product,
            working_root=staging_dir,
            leader=product_xml,
            images=roles,
        )

    def build_scene_plan(
        self, case: OtherSarSlcCase, prepared: PreparedProduct
    ) -> ScenePlan:
        """Build one import command per polarization and one scene-wide output group."""

        command_prefix = tuple(
            shlex.split(case.commands.get("import_command", "par_RSAT2_SLC"))
        )
        roles = dict(prepared.images)
        lut = roles["LUT"]
        date = prepared.product.date
        commands: list[CommandSpec] = []
        outputs: list[ProductOutput] = []
        for polarization in case.polarizations:
            relative_root = Path("products") / polarization
            staged_root = prepared.working_root / relative_root
            staged_root.mkdir(parents=True, exist_ok=True)
            slc_relative = relative_root / f"{date}.slc"
            par_relative = relative_root / f"{date}.slc.par"
            slc = prepared.working_root / slc_relative
            par = prepared.working_root / par_relative
            commands.append(
                CommandSpec(
                    f"import_{polarization.lower()}",
                    command_prefix
                    + (
                        str(prepared.leader),
                        str(lut),
                        str(roles[polarization]),
                        polarization,
                        str(par),
                        str(slc),
                    ),
                )
            )
            destination_root = case.slc_dir / f"{polarization}_SLC"
            outputs.extend(
                (
                    ProductOutput(
                        polarization, slc_relative, destination_root / slc.name
                    ),
                    ProductOutput(
                        polarization, par_relative, destination_root / par.name
                    ),
                )
            )
        state = "skip" if classify_output_group(outputs) == "complete" else "run"
        return ScenePlan(prepared.product, tuple(commands), tuple(outputs), state)

    def finalize_scene(self, prepared: PreparedProduct, plan: ScenePlan) -> None:
        """Leave RADARSAT-2 outputs unchanged because staged names are fixed."""

        return None


__all__ = ["Rsat2Adapter"]
