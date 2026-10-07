"""GAMMA 普通雷达坐标参考几何的只读规划。

功能概述:
    在执行外部命令前验证日期清单、参考 SLC、DEM 配对和目标目录，并计算当前
    ``geocoding.py`` 将要发布的关键成果路径。

函数说明:
    ``GammaRadarGeoRefPlan`` 保存不可变执行计划；
    ``build_gamma_radar_geo_ref_plan`` 只读构建计划并区分运行、跳过和残缺结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    validate_safe_date,
)

from .geo_ref_config import GammaRadarGeoRefCase
from .models import GammaSlcAsset, GammaSlcGrid
from .parameters import read_slc_grid


@dataclass(frozen=True)
class GammaRadarGeoRefPlan:
    """Validated immutable inputs and destinations for reference geocoding."""

    case: GammaRadarGeoRefCase
    dates: tuple[str, ...]
    reference_asset: GammaSlcAsset
    reference_grid: GammaSlcGrid
    dem: Path
    dem_par: Path
    root_name: str
    reference_mli: Path
    reference_mli_par: Path
    wgs84_radar_height: Path
    refined_lookup_table: Path
    incidence_map: Path
    layover_shadow_map: Path
    radar_layover_shadow_map: Path
    geocoded_mli: Path
    geocoding_quality: Path
    manifest: Path
    expected_outputs: tuple[Path, ...]
    skip: bool


def _regular_file(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"{label} not found: {path}") from error
    if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size < 1:
        raise ValueError(f"{label} must be a non-empty regular file: {path}")
    return resolved


def _read_dates(path: Path) -> tuple[str, ...]:
    source = _regular_file(path, "images inventory")
    dates = tuple(
        validate_safe_date(line.strip())
        for line in source.read_text(encoding="ascii").splitlines()
        if line.strip()
    )
    if not dates:
        raise ValueError(f"images inventory is empty: {source}")
    if len(set(dates)) != len(dates):
        raise ValueError(f"images inventory contains duplicate dates: {source}")
    return dates


def _reference_asset(case: GammaRadarGeoRefCase) -> GammaSlcAsset:
    date = case.reference_date
    polarization = case.primary_polarization
    scene = case.slc_dir / f"{polarization}_SLC" / date
    data = scene / f"{date}.slc"
    parameter = scene / f"{date}.slc.par"
    try:
        data = _regular_file(data, "reference SLC")
        parameter = _regular_file(parameter, "reference SLC parameter")
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"primary polarization {polarization} reference files are missing for {date}"
        ) from error
    return GammaSlcAsset(
        date=date,
        polarization=polarization,
        data_path=data,
        parameter_path=parameter,
    )


def build_gamma_radar_geo_ref_plan(
    case: GammaRadarGeoRefCase,
) -> GammaRadarGeoRefPlan:
    """Validate all inputs and calculate outputs without changing the filesystem."""

    dates = _read_dates(case.images_file)
    if case.reference_date not in dates:
        raise ValueError(
            f"reference date {case.reference_date} is absent from images inventory"
        )
    reference = _reference_asset(case)
    grid = read_slc_grid(reference.parameter_path)
    dem = _regular_file(case.dem, "DEM data")
    dem_par = _regular_file(case.dem_par, "DEM parameter")
    if dem_par.name != f"{dem.name}.par":
        raise ValueError(
            "DEM data and parameter names must form a pair, for example srtm.dem and srtm.dem.par"
        )

    output = case.output_dir
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise FileExistsError(f"reference-geometry destination is not a directory: {output}")
    root_name = f"{case.reference_date}.{case.primary_polarization}"
    dem_root = dem_par.name.split(".", 1)[0]
    reference_mli = output / f"{root_name}.mli"
    reference_mli_par = output / f"{root_name}.mli.par"
    wgs84_radar_height = output / f"{root_name}.wgs84.hgt"
    refined_lookup_table = output / f"{dem_root}.{root_name}.lt_fine"
    incidence_map = output / f"{dem_root}.{root_name}.inc"
    layover_shadow_map = output / f"{dem_root}.{root_name}.ls_map"
    radar_layover_shadow_map = output / f"{root_name}.ls_map_rdc"
    geocoded_mli = output / f"{dem_root}.{root_name}.mli"
    quality = output / f"{root_name}.geocoding_quality"
    manifest = output / "reference_geometry.json"
    expected = (
        reference_mli,
        reference_mli_par,
        wgs84_radar_height,
        refined_lookup_table,
        incidence_map,
        layover_shadow_map,
        radar_layover_shadow_map,
        geocoded_mli,
        quality,
        manifest,
    )
    present = tuple(path.is_file() and path.stat().st_size > 0 for path in expected)
    if output.exists() and not all(present):
        raise FileExistsError(
            f"incomplete reference-geometry destination exists and will not be removed: {output}"
        )

    return GammaRadarGeoRefPlan(
        case=case,
        dates=dates,
        reference_asset=reference,
        reference_grid=grid,
        dem=dem,
        dem_par=dem_par,
        root_name=root_name,
        reference_mli=reference_mli,
        reference_mli_par=reference_mli_par,
        wgs84_radar_height=wgs84_radar_height,
        refined_lookup_table=refined_lookup_table,
        incidence_map=incidence_map,
        layover_shadow_map=layover_shadow_map,
        radar_layover_shadow_map=radar_layover_shadow_map,
        geocoded_mli=geocoded_mli,
        geocoding_quality=quality,
        manifest=manifest,
        expected_outputs=expected,
        skip=all(present),
    )


__all__ = ["GammaRadarGeoRefPlan", "build_gamma_radar_geo_ref_plan"]
