"""GAMMA COSMO-SkyMed first- and second-generation HDF5 adapters.

功能概述:
    按 CSG/CSKS 各自命名规则发现唯一 HDF5 产品，验证日期与外部转换器，生成旧版 HH 输出布局，
    并在暂存区把转换器的波束化文件名归一为固定日期文件名。

函数说明:
    ``CsgAdapter`` 生成 ``par_CSG_SLC`` 计划；``CsksAdapter`` 生成
    ``par_CS_SLC`` 计划。
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from ..config import OtherSarSlcCase, validate_safe_date, validate_within_root
from ..models import CommandSpec, ProductOutput, ScenePlan, SourceProduct
from ..publication import classify_output_group
from .base import PreparedProduct


class _CosmoAdapter:
    sensor = ""
    marker = ""
    default_command = ""
    legacy_directory = ""

    def _command_prefix(self, case: OtherSarSlcCase) -> tuple[str, ...]:
        value = case.commands.get("import_command", self.default_command)
        prefix = tuple(shlex.split(value))
        executable = Path(prefix[0]).expanduser()
        if executable.is_absolute() or "/" in prefix[0]:
            if not executable.exists():
                raise ValueError(f"configured converter does not exist: {executable}")
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise ValueError(f"configured converter is not executable: {executable}")
        return prefix

    def _date_from_filename(self, product: Path) -> str:
        pattern = re.compile(
            rf"^{self.marker}\d?_.*_(\d{{8}})T\d{{6}}(?:_.*)?\.h5$",
            re.IGNORECASE,
        )
        match = pattern.fullmatch(product.name)
        if match is None:
            raise ValueError(
                f"{product}: expected {self.marker} filename pattern with YYYYMMDDThhmmss"
            )
        return validate_safe_date(match.group(1))

    def discover(self, case: OtherSarSlcCase) -> tuple[SourceProduct, ...]:
        """Discover one unambiguous HDF5 product per acquisition directory."""

        if case.polarizations != ("HH",):
            raise ValueError(f"{self.marker} migration currently supports only HH")
        self._command_prefix(case)
        raw_root = case.raw_dir.resolve(strict=True)
        scene_directories = sorted(path for path in raw_root.iterdir() if path.is_dir())
        if not scene_directories:
            raise ValueError(f"{raw_root}: expected exactly one HDF5 product per scene")
        products: list[SourceProduct] = []
        seen_dates: set[str] = set()
        for scene in scene_directories:
            hdf5_files = sorted(
                path
                for path in scene.rglob("*.h5")
                if path.is_file() and not path.is_symlink()
            )
            if len(hdf5_files) != 1:
                raise ValueError(
                    f"{scene}: expected exactly one HDF5 product, found {len(hdf5_files)}"
                )
            product_file = validate_within_root(hdf5_files[0], raw_root, "COSMO product")
            date = self._date_from_filename(product_file)
            if re.fullmatch(r"\d{8}", scene.name):
                directory_date = validate_safe_date(scene.name)
                if directory_date != date:
                    raise ValueError(
                        f"{product_file}: filename date {date} does not match directory date {directory_date}"
                    )
            if date in seen_dates:
                raise ValueError(f"duplicate acquisition date {date} in {raw_root}")
            seen_dates.add(date)
            products.append(
                SourceProduct(
                    sensor=self.sensor,
                    date=date,
                    root=scene.resolve(),
                    source_files=(product_file,),
                    polarizations=("HH",),
                )
            )
        return tuple(sorted(products, key=lambda product: product.date))

    def prepare(self, product: SourceProduct, staging_dir: Path) -> PreparedProduct:
        """Describe the read-only HDF5 input while keeping execution in staging."""

        if len(product.source_files) != 1:
            raise ValueError(f"{product.root}: expected one prepared HDF5 source")
        return PreparedProduct(
            product=product,
            working_root=staging_dir,
            leader=product.source_files[0],
            images=(("HH", product.source_files[0]),),
        )

    def build_scene_plan(
        self, case: OtherSarSlcCase, prepared: PreparedProduct
    ) -> ScenePlan:
        """Build one literal converter command and fixed legacy destinations."""

        date = prepared.product.date
        slc_relative = Path("products/HH") / f"{date}.slc"
        par_relative = Path("products/HH") / f"{date}.slc.par"
        destination_root = case.slc_dir / "HH_SLC" / self.legacy_directory
        outputs = (
            ProductOutput("HH", slc_relative, destination_root / f"{date}.slc"),
            ProductOutput("HH", par_relative, destination_root / f"{date}.slc.par"),
        )
        command = CommandSpec(
            "import_hh",
            self._command_prefix(case) + (str(prepared.leader), date),
        )
        state = "skip" if classify_output_group(outputs) == "complete" else "run"
        return ScenePlan(prepared.product, (command,), outputs, state)

    def finalize_scene(self, prepared: PreparedProduct, plan: ScenePlan) -> None:
        """Normalize one converter-generated SLC/parameter pair inside staging."""

        date = prepared.product.date
        slc_files = sorted(prepared.working_root.glob(f"{date}_*.slc"))
        par_files = sorted(prepared.working_root.glob(f"{date}_*.slc.par"))
        if len(slc_files) != 1:
            raise ValueError(
                f"{prepared.working_root}: expected exactly one generated SLC, found {len(slc_files)}"
            )
        if len(par_files) != 1 or par_files[0].name != f"{slc_files[0].name}.par":
            raise ValueError(
                f"{prepared.working_root}: expected exactly one matching generated SLC parameter"
            )
        slc_output = next(output for output in plan.outputs if output.staged_relative.name.endswith(".slc"))
        par_output = next(output for output in plan.outputs if output.staged_relative.name.endswith(".slc.par"))
        slc_destination = prepared.working_root / slc_output.staged_relative
        par_destination = prepared.working_root / par_output.staged_relative
        slc_destination.parent.mkdir(parents=True, exist_ok=True)
        slc_files[0].replace(slc_destination)
        par_files[0].replace(par_destination)


class CsgAdapter(_CosmoAdapter):
    """Adapter for COSMO-SkyMed Second Generation HDF5 products."""

    sensor = "csg"
    marker = "CSG"
    default_command = "par_CSG_SLC"
    legacy_directory = "CSG"


class CsksAdapter(_CosmoAdapter):
    """Adapter for COSMO-SkyMed first-generation HDF5 products."""

    sensor = "csks"
    marker = "CSKS"
    default_command = "par_CS_SLC"
    legacy_directory = "CSKS"


__all__ = ["CsgAdapter", "CsksAdapter"]
