"""GAMMA ``par_NISAR_RSLC`` 雷达网格导入适配器。

功能概述:
    只读发现 NISAR HDF5，校验所选 band/frequency/polarization 数据集，为每个极化
    规划 ``par_NISAR_RSLC`` 和快视命令，并校验真实 FCOMPLEX 格式与中心频率。

函数说明:
    ``NisarRslcAdapter`` 实现普通雷达网格 ``SensorAdapter`` 协议。
"""

from __future__ import annotations

import math
import shlex
from pathlib import Path

from geodesy_preprocessing.sar.acquisition.nisar.hdf5 import inspect_nisar_rslc

from ..config import OtherSarSlcCase, validate_safe_date, validate_within_root
from ..models import CommandSpec, ProductOutput, ScenePlan, SourceProduct
from ..publication import classify_output_group
from .base import PreparedProduct


def _gamma_value(path: Path, key: str) -> str:
    values = []
    for line in path.read_text(encoding="ascii", errors="strict").splitlines():
        head, separator, tail = line.partition(":")
        if separator and head.strip() == key:
            values.append(tail.strip())
    if len(values) != 1:
        raise ValueError(f"expected one {key} field in {path}")
    return values[0]


class NisarRslcAdapter:
    """Import selected NISAR RSLC channels without modifying source HDF5 files."""

    sensor = "nisar"

    def discover(self, case: OtherSarSlcCase) -> tuple[SourceProduct, ...]:
        """Discover HDF5 acquisitions and reject absent selected channels."""

        raw_root = case.raw_dir.resolve(strict=True)
        sources = tuple(
            sorted(
                path
                for path in raw_root.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() in {".h5", ".hdf5"}
            )
        )
        if not sources:
            raise ValueError(f"{raw_root}: no NISAR HDF5 products found")
        band = case.adapter_options["band"]
        frequency = case.adapter_options["frequency"]
        products: list[SourceProduct] = []
        dates: set[str] = set()
        stack_identity: tuple[int, int, str] | None = None
        for source in sources:
            inventory = inspect_nisar_rslc(source)
            identity = (
                inventory.track,
                inventory.frame,
                inventory.orbit_direction,
            )
            if stack_identity is None:
                stack_identity = identity
            elif identity != stack_identity:
                raise ValueError(
                    f"{raw_root}: one GAMMA stack must contain one track/frame/direction; "
                    "split NISAR products into separate input directories"
                )
            available = {
                (item.band, item.frequency, item.polarization)
                for item in inventory.channels
            }
            for polarization in case.polarizations:
                if (band, frequency, polarization) not in available:
                    raise ValueError(
                        f"{source}: selected NISAR dataset {band}/{frequency}/{polarization} is missing"
                    )
            date = validate_safe_date(inventory.acquisition_start[:10].replace("-", ""))
            if date in dates:
                raise ValueError(f"duplicate acquisition date {date} in {raw_root}")
            dates.add(date)
            products.append(
                SourceProduct(
                    sensor=self.sensor,
                    date=date,
                    root=source.resolve(),
                    source_files=(validate_within_root(source, raw_root, "NISAR source"),),
                    polarizations=case.polarizations,
                )
            )
        return tuple(sorted(products, key=lambda item: item.date))

    def prepare(self, product: SourceProduct, staging_dir: Path) -> PreparedProduct:
        """Reference the immutable HDF5 directly from a scene staging directory."""

        if len(product.source_files) != 1:
            raise ValueError(f"{product.root}: expected exactly one NISAR HDF5 source")
        return PreparedProduct(
            product=product,
            working_root=staging_dir,
            leader=product.source_files[0],
            images=(),
        )

    def build_scene_plan(
        self, case: OtherSarSlcCase, prepared: PreparedProduct
    ) -> ScenePlan:
        """Build exact NISAR import and quicklook commands for each polarization."""

        import_prefix = tuple(
            shlex.split(case.commands.get("import_command", "par_NISAR_RSLC"))
        )
        preview_prefix = tuple(shlex.split(case.commands.get("preview_command", "rasSLC")))
        options = case.adapter_options
        commands: list[CommandSpec] = []
        outputs: list[ProductOutput] = []
        date = prepared.product.date
        for polarization in case.polarizations:
            relative_directory = Path("products") / polarization / date
            root = prepared.working_root / relative_directory / date
            root.parent.mkdir(parents=True, exist_ok=True)
            slc = Path(str(root) + ".slc")
            parameter = Path(str(root) + ".slc.par")
            preview = Path(str(slc) + ".bmp")
            commands.append(
                CommandSpec(
                    f"import_{polarization.lower()}",
                    import_prefix
                    + (
                        str(prepared.leader),
                        str(root),
                        options["radiometric_calibration"],
                        options["noise_subtraction"],
                        options["band"],
                        options["frequency"],
                        polarization,
                        "0",
                    ),
                )
            )
            commands.append(
                CommandSpec(
                    f"preview_{polarization.lower()}",
                    preview_prefix + (str(slc), "{range_samples}", "1", "0", "50", "20"),
                    parameter_file=parameter,
                )
            )
            destination = case.slc_dir / f"{polarization}_SLC" / date
            for staged in (slc, parameter, preview):
                outputs.append(
                    ProductOutput(
                        polarization=polarization,
                        staged_relative=staged.relative_to(prepared.working_root),
                        destination=destination / staged.name,
                    )
                )
        state = "skip" if classify_output_group(outputs) == "complete" else "run"
        return ScenePlan(prepared.product, tuple(commands), tuple(outputs), state)

    def finalize_scene(self, prepared: PreparedProduct, plan: ScenePlan) -> None:
        """Validate FCOMPLEX output and read positive center frequencies from GAMMA parameters."""

        for output in plan.outputs:
            if not output.staged_relative.name.endswith(".slc.par"):
                continue
            parameter = prepared.working_root / output.staged_relative
            if _gamma_value(parameter, "image_format").split()[0].upper() != "FCOMPLEX":
                raise ValueError(f"NISAR GAMMA output must be FCOMPLEX: {parameter}")
            raw_frequency = _gamma_value(parameter, "center_frequency").split()[0]
            try:
                frequency = float(raw_frequency)
            except ValueError as error:
                raise ValueError(f"center_frequency must be numeric in {parameter}") from error
            if not math.isfinite(frequency) or frequency <= 0.0:
                raise ValueError(f"center_frequency must be positive in {parameter}")


__all__ = ["NisarRslcAdapter"]
