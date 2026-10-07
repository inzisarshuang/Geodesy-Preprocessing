"""GAMMA 普通雷达坐标 SLC 配准的只读逐景规划。

功能概述:
    校验参考几何、完整日期/极化网格及真实中心频率，按参考景优先顺序生成
    ``SLC_coreg.py`` 命令和明确的 RSLC 成果路径，不修改任何输入或目录。

函数说明:
    ``GammaRadarCommandPlan`` 保存一条外部命令；
    ``GammaRadarSceneOutputs`` 保存一景成果路径；
    ``GammaRadarScenePlan`` 保存参考复制或配准计划；
    ``GammaRadarCoregPlan`` 保存完整单频栈计划；
    ``build_gamma_radar_coreg_plan`` 构建只读计划。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    validate_safe_date,
)

from .coreg_config import GammaRadarCoregCase
from .models import GammaSlcAsset, GammaSlcGrid
from .parameters import read_gamma_value, read_slc_grid


@dataclass(frozen=True)
class GammaRadarCommandPlan:
    """One named external command represented as an argv tuple."""

    stage: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class GammaRadarSceneOutputs:
    """Final product paths reserved for one date and polarization."""

    rslc: Path
    rslc_par: Path
    rmli: Path
    rmli_par: Path
    lookup_table: Path
    offset_parameter: Path
    coreg_quality: Path


@dataclass(frozen=True)
class GammaRadarScenePlan:
    """One reference-copy or secondary-scene registration plan."""

    asset: GammaSlcAsset
    grid: GammaSlcGrid
    reference_asset: GammaSlcAsset
    outputs: GammaRadarSceneOutputs
    commands: tuple[GammaRadarCommandPlan, ...]
    copy_reference: bool


@dataclass(frozen=True)
class GammaRadarCoregPlan:
    """Complete ordered single-frequency GAMMA registration plan."""

    case: GammaRadarCoregCase
    dates: tuple[str, ...]
    scenes: tuple[GammaRadarScenePlan, ...]
    height_map: Path
    center_frequency_hz: float


def _file(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"{label} not found: {path}") from error
    if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size < 1:
        raise ValueError(f"{label} must be a non-empty regular file: {path}")
    return resolved


def _dates(path: Path) -> tuple[str, ...]:
    source = _file(path, "images inventory")
    values = tuple(
        validate_safe_date(line.strip())
        for line in source.read_text(encoding="ascii").splitlines()
        if line.strip()
    )
    if not values or len(set(values)) != len(values):
        raise ValueError("images inventory must contain unique dates")
    return values


def _geometry(case: GammaRadarCoregCase) -> Path:
    manifest_path = _file(case.reference_geometry, "reference geometry manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"invalid reference geometry manifest: {manifest_path}") from error
    if manifest.get("reference_date") != case.reference_date:
        raise ValueError("reference geometry reference date does not match coregistration case")
    if manifest.get("primary_polarization") != case.primary_polarization:
        raise ValueError("reference geometry primary polarization does not match coregistration case")
    output_names = manifest.get("outputs")
    if not isinstance(output_names, list):
        raise ValueError("reference geometry manifest outputs must be a list")
    heights = [value for value in output_names if isinstance(value, str) and value.endswith(".wgs84.hgt")]
    if len(heights) != 1:
        raise ValueError("reference geometry must identify exactly one WGS84 height map")
    candidate = (manifest_path.parent / heights[0]).resolve()
    if not candidate.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError("reference geometry height path escapes its directory")
    return _file(candidate, "WGS84 height map")


def _asset(case: GammaRadarCoregCase, date: str, polarization: str) -> GammaSlcAsset:
    scene = case.slc_dir / f"{polarization}_SLC" / date
    try:
        data = _file(scene / f"{date}.slc", "SLC")
        parameter = _file(scene / f"{date}.slc.par", "SLC parameter")
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"complete date/polarization grid is missing {date}/{polarization}"
        ) from error
    return GammaSlcAsset(date, polarization, data, parameter)


def _frequency(asset: GammaSlcAsset) -> float:
    raw = read_gamma_value(asset.parameter_path, "center_frequency").split()[0]
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"center frequency must be numeric in {asset.parameter_path}") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"center frequency must be positive in {asset.parameter_path}")
    return value


def _acquisition_signature(asset: GammaSlcAsset) -> tuple[tuple[str, str | None], ...]:
    text = asset.parameter_path.read_text(encoding="ascii")
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        head, separator, tail = line.partition(":")
        if separator:
            values.setdefault(head.strip(), []).append(tail.strip())
    keys = (
        "sensor",
        "orbit",
        "date",
        "start_time",
        "center_time",
        "heading",
        "near_range_slc",
        "center_range_slc",
        "far_range_slc",
        "earth_radius_below_sensor",
        "sar_to_earth_center",
    )
    return tuple(
        (key, entries[0] if len(entries := values.get(key, [])) == 1 else None)
        for key in keys
    )


def _outputs(case: GammaRadarCoregCase, date: str, polarization: str) -> GammaRadarSceneOutputs:
    directory = case.output_dir / f"{polarization}_RSLC" / date
    root = directory / date
    return GammaRadarSceneOutputs(
        rslc=root.with_suffix(".rslc"),
        rslc_par=root.with_suffix(".rslc.par"),
        rmli=root.with_suffix(".rmli"),
        rmli_par=root.with_suffix(".rmli.par"),
        lookup_table=root.with_suffix(".lt_fine"),
        offset_parameter=root.with_suffix(".off"),
        coreg_quality=root.with_suffix(".coreg_quality"),
    )


def _staged(path: Path, output: Path) -> str:
    return str(Path("{staging}") / path.relative_to(output))


def build_gamma_radar_coreg_plan(case: GammaRadarCoregCase) -> GammaRadarCoregPlan:
    """Validate a complete stack and generate reference-first command plans."""

    if case.sensor == "nisar" and (
        case.track is None or case.frame is None or case.orbit_direction is None
    ):
        raise ValueError("NISAR coregistration requires track, frame and orbit direction")
    dates = _dates(case.images_file)
    if case.reference_date not in dates:
        raise ValueError("reference date is absent from images inventory")
    ordered_dates = (case.reference_date,) + tuple(
        date for date in sorted(dates) if date != case.reference_date
    )
    ordered_polarizations = (case.primary_polarization,) + tuple(
        value
        for value in case.selected_polarizations
        if value != case.primary_polarization
    )
    height = _geometry(case)
    assets: dict[tuple[str, str], GammaSlcAsset] = {}
    grids: dict[tuple[str, str], GammaSlcGrid] = {}
    frequencies: list[float] = []
    for date in ordered_dates:
        for polarization in ordered_polarizations:
            asset = _asset(case, date, polarization)
            assets[(date, polarization)] = asset
            grids[(date, polarization)] = read_slc_grid(asset.parameter_path)
            frequencies.append(_frequency(asset))
        primary_asset = assets[(date, case.primary_polarization)]
        primary_grid = grids[(date, case.primary_polarization)]
        primary_signature = _acquisition_signature(primary_asset)
        for polarization in ordered_polarizations[1:]:
            secondary_asset = assets[(date, polarization)]
            if (
                grids[(date, polarization)] != primary_grid
                or _acquisition_signature(secondary_asset) != primary_signature
            ):
                raise ValueError(
                    "secondary polarization requires identical scene grid, orbit, and acquisition geometry"
                )
    first_frequency = frequencies[0]
    if any(not math.isclose(value, first_frequency, rel_tol=1e-9) for value in frequencies[1:]):
        raise ValueError("all SLC parameter files in one case must have the same center frequency")

    if case.output_dir.exists() and (
        case.output_dir.is_symlink() or not case.output_dir.is_dir()
    ):
        raise FileExistsError(f"RSLC destination is not a directory: {case.output_dir}")
    scenes: list[GammaRadarScenePlan] = []
    for date in ordered_dates:
        for polarization in ordered_polarizations:
            asset = assets[(date, polarization)]
            reference = assets[(case.reference_date, polarization)]
            outputs = _outputs(case, date, polarization)
            is_reference = date == case.reference_date
            commands: tuple[GammaRadarCommandPlan, ...] = ()
            if not is_reference and polarization == case.primary_polarization:
                argv = (
                    "SLC_coreg.py",
                    str(asset.data_path),
                    str(asset.parameter_path),
                    _staged(outputs.rslc, case.output_dir),
                    _staged(outputs.rslc_par, case.output_dir),
                    _staged(outputs.rmli, case.output_dir),
                    _staged(outputs.rmli_par, case.output_dir),
                    str(reference.data_path),
                    str(reference.parameter_path),
                    str(height),
                    str(case.range_looks),
                    str(case.azimuth_looks),
                    "--sar_mode",
                    str(case.sar_mode),
                    "--npoly",
                    str(case.offset_polynomial_parameters),
                    "--nr",
                    str(case.offset_range_estimates),
                    "--naz",
                    str(case.offset_azimuth_estimates),
                    "--rwin",
                    str(case.offset_range_window),
                    "--azwin",
                    str(case.offset_azimuth_window),
                    "--n_ovr",
                    str(case.offset_oversampling),
                    "--thres",
                    str(case.offset_correlation_threshold),
                )
                planned = [GammaRadarCommandPlan("standard_coreg", argv)]
                if case.refinement == "offset_field":
                    planned.append(
                        GammaRadarCommandPlan(
                            "offset_field_refine",
                            (
                                "SLC_coreg_refine.py",
                                str(asset.data_path),
                                str(asset.parameter_path),
                                _staged(outputs.rslc, case.output_dir),
                                _staged(outputs.rslc_par, case.output_dir),
                                str(reference.data_path),
                                str(reference.parameter_path),
                                "--rstep",
                                str(case.refine_range_step),
                                "--azstep",
                                str(case.refine_azimuth_step),
                                "--rwin",
                                str(case.refine_range_window),
                                "--azwin",
                                str(case.refine_azimuth_window),
                                "--n_ovr",
                                str(case.refine_oversampling),
                                "--cc_thres",
                                str(case.refine_correlation_threshold),
                                "--quality",
                                _staged(outputs.coreg_quality, case.output_dir),
                                "--offset_img",
                            ),
                        )
                    )
                commands = tuple(planned)
            elif not is_reference:
                primary_outputs = _outputs(
                    case, date, case.primary_polarization
                )
                primary_reference_outputs = _outputs(
                    case, case.reference_date, case.primary_polarization
                )
                commands = (
                    GammaRadarCommandPlan(
                        "reuse_primary_geometry",
                        (
                            "SLC_interp_lt",
                            str(asset.data_path),
                            str(reference.parameter_path),
                            str(asset.parameter_path),
                            _staged(primary_outputs.lookup_table, case.output_dir),
                            _staged(primary_reference_outputs.rmli_par, case.output_dir),
                            _staged(primary_outputs.rmli_par, case.output_dir),
                            _staged(primary_outputs.offset_parameter, case.output_dir),
                            _staged(outputs.rslc, case.output_dir),
                            _staged(outputs.rslc_par, case.output_dir),
                        ),
                    ),
                    GammaRadarCommandPlan(
                        "secondary_multi_look",
                        (
                            "multi_look",
                            _staged(outputs.rslc, case.output_dir),
                            _staged(outputs.rslc_par, case.output_dir),
                            _staged(outputs.rmli, case.output_dir),
                            _staged(outputs.rmli_par, case.output_dir),
                            str(case.range_looks),
                            str(case.azimuth_looks),
                        ),
                    ),
                )
            scenes.append(
                GammaRadarScenePlan(
                    asset=asset,
                    grid=grids[(date, polarization)],
                    reference_asset=reference,
                    outputs=outputs,
                    commands=commands,
                    copy_reference=is_reference,
                )
            )
    return GammaRadarCoregPlan(
        case=case,
        dates=ordered_dates,
        scenes=tuple(scenes),
        height_map=height,
        center_frequency_hz=first_frequency,
    )


__all__ = [
    "GammaRadarCommandPlan",
    "GammaRadarCoregPlan",
    "GammaRadarSceneOutputs",
    "GammaRadarScenePlan",
    "build_gamma_radar_coreg_plan",
]
