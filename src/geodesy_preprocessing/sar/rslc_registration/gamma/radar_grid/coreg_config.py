"""GAMMA 普通雷达坐标 SLC 配准的严格项目配置。

功能概述:
    读取单频段 RSLC 配准 case，校验卫星模式、极化、参考几何、视数和质量阈值，
    并把所有路径限制在项目根目录内。

函数说明:
    ``GammaRadarQualityThresholds`` 保存告警与失败阈值；
    ``GammaRadarCoregCase`` 保存不可变配准 case；
    ``load_gamma_radar_coreg_config`` 读取配置；
    ``select_gamma_radar_coreg_case`` 选择一个具名 case。
"""

from __future__ import annotations

import configparser
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    validate_safe_date,
    validate_within_root,
)


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_FREQUENCY = re.compile(r"[A-Za-z0-9_.-]+\Z")
_POLARIZATIONS = {"HH", "HV", "VH", "VV"}
_SENSOR_MODES = {
    "lt1": 0,
    "nisar": 1,
    "alos2": 1,
    "saocom": 1,
    "rsat2": 1,
    "cosmo": 1,
}
_REQUIRED = {
    "sensor",
    "frequency",
    "reference_date",
    "primary_polarization",
    "selected_polarizations",
    "slc_dir",
    "images_file",
    "reference_geometry",
    "output_dir",
}
_OPTIONAL = {
    "track",
    "frame",
    "orbit_direction",
    "range_looks",
    "azimuth_looks",
    "refinement",
    "sar_mode",
    "warning_offset_stddev",
    "failure_offset_stddev",
    "warning_residual_offset",
    "failure_residual_offset",
    "offset_polynomial_parameters",
    "offset_range_estimates",
    "offset_azimuth_estimates",
    "offset_range_window",
    "offset_azimuth_window",
    "offset_oversampling",
    "offset_correlation_threshold",
    "refine_range_step",
    "refine_azimuth_step",
    "refine_range_window",
    "refine_azimuth_window",
    "refine_oversampling",
    "refine_correlation_threshold",
}
_VALUE_KEYS = _REQUIRED | _OPTIONAL


@dataclass(frozen=True)
class GammaRadarQualityThresholds:
    """Warning and hard-failure limits for parsed registration quality."""

    warning_offset_stddev: float
    failure_offset_stddev: float
    warning_residual_offset: float
    failure_residual_offset: float


@dataclass(frozen=True)
class GammaRadarCoregCase:
    """One resolved single-frequency GAMMA radar-grid registration case."""

    name: str
    sensor: str
    frequency: str
    reference_date: str
    primary_polarization: str
    selected_polarizations: tuple[str, ...]
    slc_dir: Path
    images_file: Path
    reference_geometry: Path
    output_dir: Path
    range_looks: int
    azimuth_looks: int
    refinement: Literal["standard", "offset_field"]
    sar_mode: int
    quality_thresholds: GammaRadarQualityThresholds
    offset_polynomial_parameters: int = 6
    offset_range_estimates: int = 128
    offset_azimuth_estimates: int = 128
    offset_range_window: int = 256
    offset_azimuth_window: int = 256
    offset_oversampling: int = 1
    offset_correlation_threshold: float = 0.35
    refine_range_step: int = 128
    refine_azimuth_step: int = 128
    refine_range_window: int = 256
    refine_azimuth_window: int = 256
    refine_oversampling: int = 1
    refine_correlation_threshold: float = 0.35
    track: str | None = None
    frame: str | None = None
    orbit_direction: str | None = None


def _unknown(section: configparser.SectionProxy, source: Path) -> None:
    values = sorted(set(section) - _VALUE_KEYS)
    if values:
        raise ValueError(f"{source}: unknown key in [{section.name}]: {', '.join(values)}")


def _positive_integer(value: str, key: str, source: Path) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be a positive integer") from error
    if parsed < 1:
        raise ValueError(f"{source}: {key} must be a positive integer")
    return parsed


def _positive_float(value: str, key: str, source: Path) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be a positive finite number") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{source}: {key} must be a positive finite number")
    return parsed


def _integer_choice(
    value: str, key: str, choices: tuple[int, ...], source: Path
) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        allowed = ", ".join(str(item) for item in choices[:-1]) + f" or {choices[-1]}"
        raise ValueError(f"{source}: {key} must be {allowed}") from error
    if parsed not in choices:
        allowed = ", ".join(str(item) for item in choices[:-1]) + f" or {choices[-1]}"
        raise ValueError(f"{source}: {key} must be {allowed}")
    return parsed


def _window(value: str, key: str, source: Path) -> int:
    parsed = _positive_integer(value, key, source)
    if parsed < 8:
        raise ValueError(f"{source}: {key} must be at least 8")
    return parsed


def _correlation(value: str, key: str, source: Path) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be between 0 and 1") from error
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise ValueError(f"{source}: {key} must be between 0 and 1")
    return parsed


def _optional_identity_integer(
    values: dict[str, str], key: str, source: Path
) -> str | None:
    raw = values.get(key, "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be a positive integer") from error
    if parsed < 1:
        raise ValueError(f"{source}: {key} must be a positive integer")
    return str(parsed)


def _resolve(root: Path, value: str, label: str) -> Path:
    path = Path(value).expanduser()
    return validate_within_root(path if path.is_absolute() else root / path, root, label)


def _polarizations(value: str, source: Path) -> tuple[str, ...]:
    parsed = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if not parsed:
        raise ValueError(f"{source}: selected_polarizations must not be empty")
    if len(set(parsed)) != len(parsed):
        raise ValueError(f"{source}: duplicate polarization")
    unsupported = sorted(set(parsed) - _POLARIZATIONS)
    if unsupported:
        raise ValueError(f"{source}: unsupported polarization {unsupported[0]}")
    return parsed


def load_gamma_radar_coreg_config(
    path: str | Path,
) -> tuple[GammaRadarCoregCase, ...]:
    """Load ordered single-frequency radar-grid registration cases."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid GAMMA radar coreg configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    project = parser["project"]
    unknown_project = sorted(set(project) - {"root"})
    if unknown_project:
        raise ValueError(f"{source}: unknown key in [project]: {unknown_project[0]}")
    root_text = project.get("root", "").strip()
    if not root_text:
        raise ValueError(f"{source}: [project] root is required")
    root_path = Path(root_text).expanduser()
    project_root = (root_path if root_path.is_absolute() else source.parent / root_path).resolve()

    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        _unknown(parser["defaults"], source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())
    cases: list[GammaRadarCoregCase] = []
    for section_name in parser.sections():
        if section_name in {"project", "defaults"}:
            continue
        if not section_name.startswith("case:"):
            raise ValueError(f"{source}: unsupported section [{section_name}]")
        name = section_name.removeprefix("case:").strip()
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: unsafe or empty case name: {name!r}")
        section = parser[section_name]
        _unknown(section, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        missing = sorted(key for key in _REQUIRED if not values.get(key))
        if missing:
            raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")

        sensor = values["sensor"].lower()
        if sensor not in _SENSOR_MODES:
            raise ValueError(f"{source}: sensor must be one of: {', '.join(_SENSOR_MODES)}")
        frequency = values["frequency"].strip().upper()
        if not _FREQUENCY.fullmatch(frequency) or "," in frequency:
            raise ValueError(f"{source}: frequency must name a single frequency")
        selected = _polarizations(values["selected_polarizations"], source)
        track = _optional_identity_integer(values, "track", source)
        frame = _optional_identity_integer(values, "frame", source)
        raw_direction = values.get("orbit_direction", "").strip().upper()
        orbit_direction = raw_direction or None
        if orbit_direction not in {None, "ASCENDING", "DESCENDING"}:
            raise ValueError(
                f"{source}: orbit_direction must be ascending or descending"
            )
        if sensor == "nisar" and (
            track is None or frame is None or orbit_direction is None
        ):
            raise ValueError(
                f"{source}: NISAR requires track, frame and orbit_direction"
            )
        primary = values["primary_polarization"].upper()
        if primary not in selected:
            raise ValueError(f"{source}: primary polarization must be selected")
        refinement = values.get("refinement", "standard").lower()
        if refinement not in {"standard", "offset_field"}:
            raise ValueError(f"{source}: refinement must be standard or offset_field")
        expected_mode = _SENSOR_MODES[sensor]
        raw_mode = values.get("sar_mode", "auto").lower()
        if raw_mode == "auto":
            sar_mode = expected_mode
        else:
            try:
                sar_mode = int(raw_mode)
            except ValueError as error:
                raise ValueError(f"{source}: sar_mode must be auto, 0 or 1") from error
            if sar_mode not in {0, 1}:
                raise ValueError(f"{source}: sar_mode must be auto, 0 or 1")
            if sar_mode != expected_mode:
                raise ValueError(f"{source}: sar_mode conflicts with sensor {sensor}")

        warning_stddev = _positive_float(values.get("warning_offset_stddev", "0.15"), "warning_offset_stddev", source)
        failure_stddev = _positive_float(values.get("failure_offset_stddev", "0.30"), "failure_offset_stddev", source)
        warning_residual = _positive_float(values.get("warning_residual_offset", "0.20"), "warning_residual_offset", source)
        failure_residual = _positive_float(values.get("failure_residual_offset", "0.50"), "failure_residual_offset", source)
        if failure_stddev <= warning_stddev or failure_residual <= warning_residual:
            raise ValueError(f"{source}: each failure threshold must be greater than warning threshold")

        cases.append(
            GammaRadarCoregCase(
                name=name,
                sensor=sensor,
                frequency=frequency,
                reference_date=validate_safe_date(values["reference_date"]),
                primary_polarization=primary,
                selected_polarizations=selected,
                slc_dir=_resolve(project_root, values["slc_dir"], "SLC"),
                images_file=_resolve(project_root, values["images_file"], "images"),
                reference_geometry=_resolve(project_root, values["reference_geometry"], "reference geometry"),
                output_dir=_resolve(project_root, values["output_dir"], "output"),
                range_looks=_positive_integer(values.get("range_looks", "5"), "range_looks", source),
                azimuth_looks=_positive_integer(values.get("azimuth_looks", "3"), "azimuth_looks", source),
                refinement=refinement,  # type: ignore[arg-type]
                sar_mode=sar_mode,
                quality_thresholds=GammaRadarQualityThresholds(
                    warning_stddev,
                    failure_stddev,
                    warning_residual,
                    failure_residual,
                ),
                offset_polynomial_parameters=_integer_choice(
                    values.get("offset_polynomial_parameters", "6"),
                    "offset_polynomial_parameters", (1, 3, 4, 6), source,
                ),
                offset_range_estimates=_positive_integer(
                    values.get("offset_range_estimates", "128"), "offset_range_estimates", source
                ),
                offset_azimuth_estimates=_positive_integer(
                    values.get("offset_azimuth_estimates", "128"), "offset_azimuth_estimates", source
                ),
                offset_range_window=_window(
                    values.get("offset_range_window", "256"), "offset_range_window", source
                ),
                offset_azimuth_window=_window(
                    values.get("offset_azimuth_window", "256"), "offset_azimuth_window", source
                ),
                offset_oversampling=_integer_choice(
                    values.get("offset_oversampling", "1"), "offset_oversampling", (1, 2, 4), source
                ),
                offset_correlation_threshold=_correlation(
                    values.get("offset_correlation_threshold", "0.35"),
                    "offset_correlation_threshold", source,
                ),
                refine_range_step=_positive_integer(
                    values.get("refine_range_step", "128"), "refine_range_step", source
                ),
                refine_azimuth_step=_positive_integer(
                    values.get("refine_azimuth_step", "128"), "refine_azimuth_step", source
                ),
                refine_range_window=_window(
                    values.get("refine_range_window", "256"), "refine_range_window", source
                ),
                refine_azimuth_window=_window(
                    values.get("refine_azimuth_window", "256"), "refine_azimuth_window", source
                ),
                refine_oversampling=_integer_choice(
                    values.get("refine_oversampling", "1"), "refine_oversampling", (1, 2, 4), source
                ),
                refine_correlation_threshold=_correlation(
                    values.get("refine_correlation_threshold", "0.35"),
                    "refine_correlation_threshold", source,
                ),
                track=track,
                frame=frame,
                orbit_direction=orbit_direction,
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def select_gamma_radar_coreg_case(
    cases: Sequence[GammaRadarCoregCase], name: str | None
) -> GammaRadarCoregCase:
    """Select one case, rejecting an absent or ambiguous name."""

    if name is None:
        if len(cases) != 1:
            raise ValueError("--case is required when configuration has multiple cases")
        return cases[0]
    for case in cases:
        if case.name == name:
            return case
    raise ValueError(f"unknown GAMMA radar coreg case {name!r}")


__all__ = [
    "GammaRadarCoregCase",
    "GammaRadarQualityThresholds",
    "load_gamma_radar_coreg_config",
    "select_gamma_radar_coreg_case",
]
