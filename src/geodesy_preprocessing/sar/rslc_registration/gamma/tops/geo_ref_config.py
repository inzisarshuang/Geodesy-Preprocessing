"""Project-local configuration for GAMMA TOPS reference DEM geocoding.

功能概述:
    读取项目级严格 INI 配置，合并公共默认值，并把具名 case 转换为历史
    S1-04 位置参数。

函数说明:
    ``GammaTopsGeoRefCase``（兼容名 ``GeoRefConfigCase``）保存 case；``load_geo_ref_config`` 读取配置；
    ``select_geo_ref_case`` 选择 case；``geo_ref_case_arguments`` 生成兼容参数。
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.case_layout import optional_case_layout


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_DEFAULT_KEYS = {
    "dem_dir",
    "range_looks", "azimuth_looks", "latitude_oversampling",
    "longitude_oversampling", "reference_flag", "incidence_flag",
}
_CASE_KEYS = _DEFAULT_KEYS | {
    "case_root", "reference", "dem_dir", "slc_dir", "output_dir"
}
_REQUIRED = _CASE_KEYS - {"case_root"}


@dataclass(frozen=True)
class GammaTopsGeoRefCase:
    """One resolved project-specific DEM-geocoding case."""

    name: str
    reference: str
    dem_dir: Path
    slc_dir: Path
    output_dir: Path
    range_looks: int
    azimuth_looks: int
    latitude_oversampling: float | None
    longitude_oversampling: float | None
    reference_flag: int
    incidence_flag: int
    case_root: Path | None = None


GeoRefConfigCase = GammaTopsGeoRefCase


def _unknown(section: configparser.SectionProxy, allowed: set[str], source: Path) -> None:
    keys = sorted(set(section) - allowed)
    if keys:
        raise ValueError(f"{source}: unknown key in [{section.name}]: {', '.join(keys)}")


def _integer(value: str, label: str, source: Path, *, binary: bool = False) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{source}: {label} must be an integer") from error
    valid = parsed in {0, 1} if binary else parsed > 0
    if not valid:
        suffix = "0 or 1" if binary else "positive"
        raise ValueError(f"{source}: {label} must be {suffix}")
    return parsed


def _oversampling(value: str, label: str, source: Path) -> float | None:
    if value.lower() in {"auto", "-"}:
        return None
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{source}: {label} must be positive or auto") from error
    if parsed <= 0:
        raise ValueError(f"{source}: {label} must be positive or auto")
    return parsed


def load_geo_ref_config(path: str | Path) -> tuple[GeoRefConfigCase, ...]:
    """Load ordered S1-04 cases from one strict project-local config."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid DEM-geocoding configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    _unknown(parser["project"], {"root"}, source)
    root_text = parser["project"].get("root", "").strip()
    if not root_text:
        raise ValueError(f"{source}: [project] root is required")
    project_root = (source.parent / root_text).resolve()
    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        _unknown(parser["defaults"], _DEFAULT_KEYS, source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())
    cases: list[GeoRefConfigCase] = []
    for section_name in parser.sections():
        if section_name in {"project", "defaults"}:
            continue
        if not section_name.startswith("case:"):
            raise ValueError(f"{source}: unsupported section [{section_name}]")
        name = section_name.removeprefix("case:").strip()
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: unsafe or empty case name: {name!r}")
        section = parser[section_name]
        _unknown(section, _CASE_KEYS, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        missing = sorted(key for key in _REQUIRED if not values.get(key))
        if missing:
            raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")
        latitude = _oversampling(values["latitude_oversampling"], "latitude oversampling", source)
        longitude = _oversampling(values["longitude_oversampling"], "longitude oversampling", source)
        if (latitude is None) != (longitude is None):
            raise ValueError(f"{source}: oversampling values must both be auto or both numeric")

        def resolved(key: str) -> Path:
            value = Path(values[key]).expanduser()
            return (value if value.is_absolute() else project_root / value).resolve()

        dem_dir = resolved("dem_dir")
        slc_dir = resolved("slc_dir")
        output_dir = resolved("output_dir")
        layout = optional_case_layout(project_root, values)
        if layout is not None:
            dem_dir = layout.require_project_input(dem_dir, "dem_dir")
            slc_dir = layout.require_product(slc_dir, "SLC", "slc_dir")
            output_dir = layout.require_product(
                output_dir, "GEOMETRY", "output_dir"
            )

        cases.append(
            GeoRefConfigCase(
                name=name,
                reference=values["reference"],
                dem_dir=dem_dir,
                slc_dir=slc_dir,
                output_dir=output_dir,
                range_looks=_integer(values["range_looks"], "range looks", source),
                azimuth_looks=_integer(values["azimuth_looks"], "azimuth looks", source),
                latitude_oversampling=latitude,
                longitude_oversampling=longitude,
                reference_flag=_integer(values["reference_flag"], "reference flag", source, binary=True),
                incidence_flag=_integer(values["incidence_flag"], "incidence flag", source, binary=True),
                case_root=None if layout is None else layout.case_root,
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def select_geo_ref_case(cases: Sequence[GeoRefConfigCase], name: str | None) -> GeoRefConfigCase:
    """Select one named case, rejecting ambiguous or unknown selection."""

    if name is None:
        if len(cases) != 1:
            raise ValueError("--case is required when configuration has multiple cases")
        return cases[0]
    for case in cases:
        if case.name == name:
            return case
    raise ValueError(f"unknown DEM-geocoding case {name!r}")


def geo_ref_case_arguments(case: GeoRefConfigCase) -> tuple[str, ...]:
    """Return the ten historical S1-04 arguments for one case."""

    return (
        case.reference,
        str(case.dem_dir),
        str(case.slc_dir),
        str(case.output_dir),
        str(case.range_looks),
        str(case.azimuth_looks),
        "-" if case.latitude_oversampling is None else str(case.latitude_oversampling),
        "-" if case.longitude_oversampling is None else str(case.longitude_oversampling),
        str(case.reference_flag),
        str(case.incidence_flag),
    )


__all__ = ["GammaTopsGeoRefCase", "GeoRefConfigCase", "geo_ref_case_arguments", "load_geo_ref_config", "select_geo_ref_case"]
