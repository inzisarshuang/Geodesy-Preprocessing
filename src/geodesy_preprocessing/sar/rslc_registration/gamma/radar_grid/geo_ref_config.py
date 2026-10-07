"""GAMMA 普通雷达坐标参考几何的严格项目配置。

功能概述:
    读取 ``[project]``、``[defaults]`` 与 ``[case:name]``，校验参考影像、DEM、
    视数及 ``geocoding.py`` 数值参数，并将所有路径限制在项目根目录内。

函数说明:
    ``GammaRadarGeoRefCase`` 保存不可变配置；
    ``load_gamma_radar_geo_ref_config`` 读取全部 case；
    ``select_gamma_radar_geo_ref_case`` 明确选择一个 case。
"""

from __future__ import annotations

import configparser
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    validate_safe_date,
    validate_within_root,
)


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_POLARIZATION = re.compile(r"[A-Z][A-Z0-9]*\Z")
_REQUIRED_KEYS = {
    "reference_date",
    "primary_polarization",
    "slc_dir",
    "images_file",
    "dem",
    "dem_par",
    "output_dir",
}
_NUMERIC_KEYS = {
    "range_looks",
    "azimuth_looks",
    "latitude_oversampling",
    "longitude_oversampling",
    "post_spacing",
    "correlation_threshold",
    "polynomial_parameters",
    "range_offset_estimates",
    "azimuth_offset_estimates",
    "range_window",
    "azimuth_window",
    "area_factor",
}
_VALUE_KEYS = _REQUIRED_KEYS | _NUMERIC_KEYS


@dataclass(frozen=True)
class GammaRadarGeoRefCase:
    """One resolved ordinary radar-grid reference-geometry case."""

    name: str
    reference_date: str
    primary_polarization: str
    slc_dir: Path
    images_file: Path
    dem: Path
    dem_par: Path
    output_dir: Path
    range_looks: int
    azimuth_looks: int
    latitude_oversampling: float | None
    longitude_oversampling: float | None
    post_spacing: float | None
    correlation_threshold: float
    polynomial_parameters: int
    range_offset_estimates: int | None
    azimuth_offset_estimates: int | None
    range_window: int
    azimuth_window: int
    area_factor: float


def _reject_unknown(
    section: configparser.SectionProxy, allowed: set[str], source: Path
) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ValueError(
            f"{source}: unknown key in [{section.name}]: {', '.join(unknown)}"
        )


def _positive_integer(value: str, label: str, source: Path) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{source}: {label} must be a positive integer") from error
    if parsed < 1:
        raise ValueError(f"{source}: {label} must be a positive integer")
    return parsed


def _optional_positive_integer(
    values: dict[str, str], key: str, source: Path
) -> int | None:
    raw = values.get(key, "").strip()
    return None if not raw or raw.lower() in {"auto", "-"} else _positive_integer(raw, key, source)


def _positive_float(value: str, label: str, source: Path) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{source}: {label} must be a positive finite number") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{source}: {label} must be a positive finite number")
    return parsed


def _optional_positive_float(
    values: dict[str, str], key: str, source: Path
) -> float | None:
    raw = values.get(key, "").strip()
    return None if not raw or raw.lower() in {"auto", "-"} else _positive_float(raw, key, source)


def _resolve(root: Path, value: str, label: str) -> Path:
    path = Path(value).expanduser()
    return validate_within_root(path if path.is_absolute() else root / path, root, label)


def load_gamma_radar_geo_ref_config(
    path: str | Path,
) -> tuple[GammaRadarGeoRefCase, ...]:
    """Load ordered reference-geometry cases from one strict INI file."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid GAMMA radar geo-ref configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    _reject_unknown(parser["project"], {"root"}, source)
    root_text = parser["project"].get("root", "").strip()
    if not root_text:
        raise ValueError(f"{source}: [project] root is required")
    raw_root = Path(root_text).expanduser()
    project_root = (
        raw_root if raw_root.is_absolute() else source.parent / raw_root
    ).resolve()

    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        _reject_unknown(parser["defaults"], _VALUE_KEYS, source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())

    cases: list[GammaRadarGeoRefCase] = []
    for section_name in parser.sections():
        if section_name in {"project", "defaults"}:
            continue
        if not section_name.startswith("case:"):
            raise ValueError(f"{source}: unsupported section [{section_name}]")
        name = section_name.removeprefix("case:").strip()
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: unsafe or empty case name: {name!r}")
        section = parser[section_name]
        _reject_unknown(section, _VALUE_KEYS, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        missing = sorted(key for key in _REQUIRED_KEYS if not values.get(key))
        if missing:
            raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")

        polarization = values["primary_polarization"].upper()
        if not _POLARIZATION.fullmatch(polarization):
            raise ValueError(f"{source}: invalid primary_polarization")
        latitude = _optional_positive_float(values, "latitude_oversampling", source)
        longitude = _optional_positive_float(values, "longitude_oversampling", source)
        if (latitude is None) != (longitude is None):
            raise ValueError(f"{source}: oversampling values must both be auto or both numeric")
        threshold = _positive_float(
            values.get("correlation_threshold", "0.2"),
            "correlation_threshold",
            source,
        )
        if threshold > 1:
            raise ValueError(f"{source}: correlation_threshold must be between 0 and 1")
        polynomial = _positive_integer(
            values.get("polynomial_parameters", "1"),
            "polynomial_parameters",
            source,
        )
        if polynomial not in {1, 3, 4, 6}:
            raise ValueError(f"{source}: polynomial_parameters must be 1, 3, 4 or 6")

        cases.append(
            GammaRadarGeoRefCase(
                name=name,
                reference_date=validate_safe_date(values["reference_date"]),
                primary_polarization=polarization,
                slc_dir=_resolve(project_root, values["slc_dir"], "SLC"),
                images_file=_resolve(project_root, values["images_file"], "images"),
                dem=_resolve(project_root, values["dem"], "DEM"),
                dem_par=_resolve(project_root, values["dem_par"], "DEM parameter"),
                output_dir=_resolve(project_root, values["output_dir"], "output"),
                range_looks=_positive_integer(values.get("range_looks", "10"), "range_looks", source),
                azimuth_looks=_positive_integer(values.get("azimuth_looks", "10"), "azimuth_looks", source),
                latitude_oversampling=latitude,
                longitude_oversampling=longitude,
                post_spacing=_optional_positive_float(values, "post_spacing", source),
                correlation_threshold=threshold,
                polynomial_parameters=polynomial,
                range_offset_estimates=_optional_positive_integer(values, "range_offset_estimates", source),
                azimuth_offset_estimates=_optional_positive_integer(values, "azimuth_offset_estimates", source),
                range_window=_positive_integer(values.get("range_window", "128"), "range_window", source),
                azimuth_window=_positive_integer(values.get("azimuth_window", "128"), "azimuth_window", source),
                area_factor=_positive_float(values.get("area_factor", "1.0"), "area_factor", source),
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def select_gamma_radar_geo_ref_case(
    cases: Sequence[GammaRadarGeoRefCase], name: str | None
) -> GammaRadarGeoRefCase:
    """Select a named case, or the sole configured case when unambiguous."""

    if name is None:
        if len(cases) != 1:
            raise ValueError("--case is required when configuration has multiple cases")
        return cases[0]
    for case in cases:
        if case.name == name:
            return case
    available = ", ".join(case.name for case in cases)
    raise ValueError(f"unknown GAMMA radar geo-ref case {name!r}; available: {available}")


__all__ = [
    "GammaRadarGeoRefCase",
    "load_gamma_radar_geo_ref_config",
    "select_gamma_radar_geo_ref_case",
]
