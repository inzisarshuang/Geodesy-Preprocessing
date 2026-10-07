"""GAMMA LT-1 TIFF and metadata adapter.

功能概述:
    按共同文件干匹配 LT-1 TIFF/XML，解析唯一日期，并把导入、原参数保留、隔离轨道滤波和预览
    组织为固定顺序的暂存计划。

函数说明:
    ``Lt1Adapter`` 实现 LT-1 产品发现、暂存描述和安全 GAMMA 命令计划。
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..config import OtherSarSlcCase, validate_safe_date, validate_within_root
from ..models import CommandSpec, ProductOutput, ScenePlan, SourceProduct
from ..publication import classify_output_group
from .base import PreparedProduct

_DATE_PATTERN = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def _unique_date(*values: str) -> str:
    candidates = {match for value in values for match in _DATE_PATTERN.findall(value)}
    valid: set[str] = set()
    for candidate in candidates:
        valid.add(validate_safe_date(candidate))
    if len(valid) != 1:
        raise ValueError(f"expected one LT-1 acquisition date, found: {sorted(valid)}")
    return next(iter(valid))


def _prefix(case: OtherSarSlcCase, key: str, default: str) -> tuple[str, ...]:
    return tuple(shlex.split(case.commands.get(key, default)))


class Lt1Adapter:
    """Adapt structurally paired LT-1 products to an isolated SLC plan."""

    sensor = "lt1"

    def discover(self, case: OtherSarSlcCase) -> tuple[SourceProduct, ...]:
        """Discover exactly one matching TIFF/XML pair per acquisition directory."""

        if case.polarizations != ("HH",):
            raise ValueError("LT-1 migration currently supports only HH")
        raw_root = case.raw_dir.resolve(strict=True)
        scene_directories = sorted(path for path in raw_root.iterdir() if path.is_dir())
        if not scene_directories:
            raise ValueError(f"{raw_root}: no LT-1 acquisition directories found")
        products: list[SourceProduct] = []
        seen_dates: set[str] = set()
        for scene in scene_directories:
            images = sorted(
                path
                for path in scene.iterdir()
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".tif", ".tiff"}
            )
            metadata = sorted(
                path
                for path in scene.iterdir()
                if path.is_file() and not path.is_symlink() and path.name.lower().endswith(".meta.xml")
            )
            if len(images) != 1:
                raise ValueError(f"{scene}: expected exactly one TIFF, found {len(images)}")
            if len(metadata) != 1:
                raise ValueError(f"{scene}: expected exactly one metadata XML, found {len(metadata)}")
            image_stem = images[0].name[: -len(images[0].suffix)]
            metadata_stem = metadata[0].name[: -len(".meta.xml")]
            if image_stem != metadata_stem:
                raise ValueError(f"{scene}: expected matching TIFF and metadata stems")
            image = validate_within_root(images[0], raw_root, "LT-1 TIFF")
            xml = validate_within_root(metadata[0], raw_root, "LT-1 metadata")
            date = _unique_date(scene.name, image.name, xml.name, xml.read_text(encoding="utf-8"))
            if date in seen_dates:
                raise ValueError(f"duplicate acquisition date {date} in {raw_root}")
            seen_dates.add(date)
            products.append(
                SourceProduct(
                    sensor=self.sensor,
                    date=date,
                    root=scene.resolve(),
                    source_files=(image, xml),
                    polarizations=("HH",),
                )
            )
        return tuple(sorted(products, key=lambda product: product.date))

    def prepare(self, product: SourceProduct, staging_dir: Path) -> PreparedProduct:
        """Keep TIFF/XML read-only and describe their roles for staged execution."""

        if len(product.source_files) != 2:
            raise ValueError(f"{product.root}: expected one LT-1 TIFF/XML pair")
        image, metadata = product.source_files
        return PreparedProduct(
            product=product,
            working_root=staging_dir,
            leader=image,
            images=(("METADATA", metadata),),
        )

    def build_scene_plan(
        self, case: OtherSarSlcCase, prepared: PreparedProduct
    ) -> ScenePlan:
        """Build import, isolated orbit filtering, and preview commands."""

        date = prepared.product.date
        stage = prepared.working_root
        work = stage / "work"
        products = stage / "products"
        orbit_png = stage / "orbit_png"
        work.mkdir(parents=True, exist_ok=True)
        products.mkdir(parents=True, exist_ok=True)
        orbit_png.mkdir(parents=True, exist_ok=True)
        metadata = dict(prepared.images)["METADATA"]
        work_par = work / f"{date}.slc.par"
        orbit_input = work / f"{date}.orbit-input.slc.par"
        slc = products / f"{date}.slc"
        filtered = products / f"{date}.slc.par"
        original = products / f"{date}.slc.par.orig"
        preview = products / f"{date}.slc.bmp"
        commands = (
            CommandSpec(
                "import_hh",
                _prefix(case, "import_command", "par_LT1_SLC")
                + (str(prepared.leader), str(metadata), str(work_par), str(slc), "1"),
            ),
            CommandSpec("preserve_original", ("cp", "--", str(work_par), str(original))),
            CommandSpec("isolate_orbit_input", ("cp", "--", str(work_par), str(orbit_input))),
            CommandSpec(
                "orbit_filter",
                _prefix(case, "orbit_filter_command", "ORB_filt_spline.py")
                + (
                    str(orbit_input),
                    str(filtered),
                    "--png",
                    str(orbit_png),
                    "--degree",
                    "5",
                ),
            ),
            CommandSpec(
                "preview",
                _prefix(case, "preview_command", "rasSLC")
                + (
                    str(slc),
                    "{range_samples}",
                    "1",
                    "0",
                    "10",
                    "20",
                    "1",
                    "0.35",
                    "-",
                    "1",
                ),
                parameter_file=filtered,
            ),
        )
        relative_outputs = (
            ("HH", Path("products") / slc.name, case.slc_dir / slc.name),
            ("HH", Path("products") / filtered.name, case.slc_dir / filtered.name),
            ("HH", Path("products") / original.name, case.slc_dir / original.name),
            ("HH", Path("products") / preview.name, case.slc_dir / preview.name),
        )
        outputs = tuple(ProductOutput(*values) for values in relative_outputs)
        state = "skip" if classify_output_group(outputs) == "complete" else "run"
        return ScenePlan(prepared.product, commands, outputs, state)

    def finalize_scene(self, prepared: PreparedProduct, plan: ScenePlan) -> None:
        """Leave LT-1 products unchanged because every staged name is fixed."""

        return None


__all__ = ["Lt1Adapter"]
