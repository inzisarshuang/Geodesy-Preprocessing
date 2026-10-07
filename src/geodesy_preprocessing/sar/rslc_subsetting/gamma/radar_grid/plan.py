"""Read-only planning for KML-driven GAMMA registered-stack cropping.

功能概述:
    在运行 GAMMA 前验证注册栈、参考几何、KML、极化和输出边界。

函数说明:
    ``GammaRadarCropPlan`` 保存完整不可变输入计划；
    ``build_crop_plan`` 验证全部输入但不创建处理产品。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from geodesy_preprocessing.sar.common.models import (
    GeometryType,
    RegisteredStackAsset,
    RegisteredStackManifest,
)
from geodesy_preprocessing.sar.common.roi import (
    buffer_wgs84_geometry,
    load_kml_roi,
)
from geodesy_preprocessing.sar.common.stack_manifest import read_stack_manifest

from .config import GammaRadarCropConfig
from .models import GammaMapGrid, GammaRadarGrid
from .parameters import read_gamma_map_grid, read_gamma_radar_grid


@dataclass(frozen=True)
class GammaRadarCropPlan:
    """Validated immutable inputs for one registered-stack crop."""

    config: GammaRadarCropConfig
    source_root: Path
    output_dir: Path
    manifest: RegisteredStackManifest
    selected_polarizations: tuple[str, ...]
    assets: tuple[RegisteredStackAsset, ...]
    asset_grids: tuple[GammaRadarGrid, ...]
    map_grid: GammaMapGrid
    mli_grid: GammaRadarGrid
    rslc_grid: GammaRadarGrid
    lookup_table: Path
    roi: BaseGeometry
    buffered_roi: BaseGeometry
    existing_complete: bool


def _nonempty_file(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"missing {label}: {path}") from error
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"{label} must be a non-empty file: {resolved}")
    return resolved


def _grid_identity(grid: GammaRadarGrid) -> tuple[int, int, str]:
    return grid.width, grid.nlines, grid.image_format


def build_crop_plan(
    config: GammaRadarCropConfig,
    *,
    allow_incomplete_output: bool = False,
) -> GammaRadarCropPlan:
    """Validate a crop case without running GAMMA or creating output products."""

    if not isinstance(config, GammaRadarCropConfig):
        raise TypeError("config must be GammaRadarCropConfig")
    manifest_path = _nonempty_file(config.registered_stack, "registered stack manifest")
    manifest = read_stack_manifest(manifest_path)
    if manifest.geometry_type is not GeometryType.RADAR_GRID:
        raise ValueError("GAMMA radar-grid crop requires a radar_grid manifest")
    source_root = manifest_path.parent.resolve()
    output = config.output_dir.resolve()
    if output == source_root or output.is_relative_to(source_root):
        raise ValueError("crop output must not be the source stack or inside it")
    existing_complete = False
    if output.exists():
        if not output.is_dir():
            raise FileExistsError(f"crop output conflicts with a file: {output}")
        existing_complete = all(
            (output / name).is_file()
            for name in ("registered_stack.json", "crop_window.json")
        )
        if not existing_complete and not allow_incomplete_output:
            raise FileExistsError(f"incomplete or conflicting crop output exists: {output}")

    selected = manifest.polarizations if config.polarizations is None else config.polarizations
    missing = tuple(value for value in selected if value not in manifest.polarizations)
    if missing:
        raise ValueError(f"selected polarizations are missing from stack: {', '.join(missing)}")
    assets_by_identity = {
        (asset.date, asset.polarization): asset for asset in manifest.assets
    }
    assets = tuple(
        assets_by_identity[(date, polarization)]
        for date in manifest.dates
        for polarization in selected
    )
    asset_grids: list[GammaRadarGrid] = []
    for asset in assets:
        _nonempty_file(source_root / asset.data_path, "RSLC asset")
        parameter = _nonempty_file(source_root / asset.parameter_path, "RSLC parameter")
        grid = read_gamma_radar_grid(parameter)
        if grid.acquisition_date is not None and grid.acquisition_date != asset.date:
            raise ValueError(f"RSLC parameter date does not match asset {asset.date}: {parameter}")
        if grid.polarization is not None and grid.polarization != asset.polarization:
            raise ValueError(
                f"RSLC parameter polarization does not match asset {asset.polarization}: {parameter}"
            )
        if grid.image_format not in {"FCOMPLEX", "SCOMPLEX"}:
            raise ValueError(f"RSLC image_format must be FCOMPLEX or SCOMPLEX: {parameter}")
        asset_grids.append(grid)
    first = asset_grids[0]
    if any(_grid_identity(grid) != _grid_identity(first) for grid in asset_grids[1:]):
        raise ValueError("all selected RSLC assets must use one common grid and format")

    reference = manifest.reference_date
    geometry = config.geometry_dir.resolve(strict=True)
    map_parameter = _nonempty_file(
        geometry / f"{reference}.utm.dem.par", "reference map parameter"
    )
    mli_parameter = _nonempty_file(
        geometry / f"{reference}.mli.par", "reference MLI parameter"
    )
    lookup = _nonempty_file(geometry / f"{reference}.lt_fine", "reference lt_fine")
    map_grid = read_gamma_map_grid(map_parameter)
    mli_grid = read_gamma_radar_grid(mli_parameter)
    if mli_grid.acquisition_date is not None and mli_grid.acquisition_date != reference:
        raise ValueError("reference MLI date does not match manifest reference date")
    if (
        first.width // mli_grid.range_looks != mli_grid.width
        or first.nlines // mli_grid.azimuth_looks != mli_grid.nlines
    ):
        raise ValueError("reference MLI looks do not match the registered RSLC grid")
    expected_lookup_size = map_grid.width * map_grid.nlines * 8
    if lookup.stat().st_size != expected_lookup_size:
        raise ValueError(
            f"lt_fine size does not match map grid: expected {expected_lookup_size} bytes"
        )
    kml = _nonempty_file(config.kml_file, "KML ROI")
    roi = load_kml_roi(kml, config.kml_placemark)
    buffered = buffer_wgs84_geometry(roi, config.buffer_km * 1000.0)
    return GammaRadarCropPlan(
        config=config,
        source_root=source_root,
        output_dir=output,
        manifest=manifest,
        selected_polarizations=tuple(selected),
        assets=assets,
        asset_grids=tuple(asset_grids),
        map_grid=map_grid,
        mli_grid=mli_grid,
        rslc_grid=first,
        lookup_table=lookup,
        roi=roi,
        buffered_roi=buffered,
        existing_complete=existing_complete,
    )


__all__ = ["GammaRadarCropPlan", "build_crop_plan"]
