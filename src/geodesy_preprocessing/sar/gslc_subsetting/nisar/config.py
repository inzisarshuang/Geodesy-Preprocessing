"""NISAR GSLC 原生复数栈的严格项目配置。

功能概述:
    解析一个或多个 LA/LB case，检查科学选择、日期、项目目录边界和输入输出隔离。

函数说明:
    ``load_nisar_gslc_stack_config`` 读取全部 case；
    ``select_nisar_gslc_stack_case`` 按名称选择一个 case。
"""

from __future__ import annotations

import configparser
import math
import re
from pathlib import Path

from geodesy_preprocessing.common.paths import validate_within_root

from .models import NisarGslcStackCase


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_DATE = re.compile(r"\d{8}\Z")
_POLARIZATIONS = {"HH", "HV", "VH", "VV"}
_DEFAULT_KEYS = {
    "source_manifest",
    "kml_file",
    "kml_placemark",
    "buffer_km",
    "band",
    "frequency",
    "polarizations",
    "reference_date",
    "output_dir",
}
_REQUIRED = {
    "source_manifest",
    "kml_file",
    "buffer_km",
    "band",
    "frequency",
    "polarizations",
    "reference_date",
    "output_dir",
}
_CREDENTIAL_FRAGMENTS = ("password", "passwd", "token", "secret", "api_key", "cookie")


def _reject_keys(section: configparser.SectionProxy, allowed: set[str], source: Path) -> None:
    keys = set(section)
    credentials = sorted(
        key for key in keys if any(fragment in key.lower() for fragment in _CREDENTIAL_FRAGMENTS)
    )
    if credentials:
        raise ValueError(
            f"{source}: credential fields are forbidden in [{section.name}]: "
            f"{', '.join(credentials)}"
        )
    unknown = sorted(keys - allowed)
    if unknown:
        raise ValueError(f"{source}: unknown key in [{section.name}]: {', '.join(unknown)}")


def _path(value: str, root: Path, label: str) -> Path:
    candidate = Path(value).expanduser()
    resolved = candidate if candidate.is_absolute() else root / candidate
    return validate_within_root(resolved, root, label)


def _buffer(value: str, source: Path) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise ValueError(f"{source}: buffer_km must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{source}: buffer_km must be finite")
    if result < 0.0:
        raise ValueError(f"{source}: buffer_km must be non-negative")
    return result


def _polarizations(value: str, source: Path) -> tuple[str, ...]:
    result = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{source}: polarizations must contain unique labels")
    unsupported = sorted(set(result) - _POLARIZATIONS)
    if unsupported:
        raise ValueError(f"{source}: unsupported polarization {unsupported[0]}")
    return result


def load_nisar_gslc_stack_config(path: str | Path) -> tuple[NisarGslcStackCase, ...]:
    """Load every native GSLC stack case from one strict INI file."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid NISAR GSLC stack configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    _reject_keys(parser["project"], {"root"}, source)
    root_value = parser["project"].get("root", "").strip()
    if not root_value:
        raise ValueError(f"{source}: [project] root is required")
    root_candidate = Path(root_value).expanduser()
    project_root = (
        root_candidate if root_candidate.is_absolute() else source.parent / root_candidate
    ).resolve()

    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        _reject_keys(parser["defaults"], _DEFAULT_KEYS, source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())
    invalid_sections = sorted(
        name for name in parser.sections() if name not in {"project", "defaults"} and not name.startswith("case:")
    )
    if invalid_sections:
        raise ValueError(f"{source}: unknown section [{invalid_sections[0]}]")

    cases: list[NisarGslcStackCase] = []
    for section_name in parser.sections():
        if not section_name.startswith("case:"):
            continue
        name = section_name.removeprefix("case:")
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: invalid GSLC stack case name {name!r}")
        section = parser[section_name]
        _reject_keys(section, _DEFAULT_KEYS, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        missing = sorted(key for key in _REQUIRED if not values.get(key))
        if missing:
            raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")
        band = values["band"].upper()
        if band not in {"L", "S"}:
            raise ValueError(f"{source}: band must be L or S")
        frequency = values["frequency"].upper()
        if frequency not in {"A", "B"}:
            raise ValueError(f"{source}: frequency must be exactly A or B")
        reference_date = values["reference_date"]
        if not _DATE.fullmatch(reference_date):
            raise ValueError(f"{source}: reference_date must use YYYYMMDD")
        source_manifest = _path(values["source_manifest"], project_root, "source_manifest")
        kml_file = _path(values["kml_file"], project_root, "kml_file")
        output_dir = _path(values["output_dir"], project_root, "output_dir")
        source_dir = source_manifest.parent
        if output_dir == source_dir or output_dir.is_relative_to(source_dir):
            raise ValueError("GSLC stack output must be separate from the source product directory")
        cases.append(
            NisarGslcStackCase(
                name=name,
                project_root=project_root,
                source_manifest=source_manifest,
                kml_file=kml_file,
                kml_placemark=values.get("kml_placemark", "").strip() or None,
                buffer_km=_buffer(values["buffer_km"], source),
                band=band,
                frequency=frequency,
                polarizations=_polarizations(values["polarizations"], source),
                reference_date=reference_date,
                output_dir=output_dir,
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:NAME] section is required")
    outputs = [case.output_dir for case in cases]
    if len(outputs) != len(set(outputs)):
        raise ValueError(f"{source}: every GSLC stack case requires a distinct output_dir")
    return tuple(cases)


def select_nisar_gslc_stack_case(
    cases: tuple[NisarGslcStackCase, ...], name: str | None
) -> NisarGslcStackCase:
    """Select one named case, allowing omission only when exactly one exists."""

    if name is None:
        if len(cases) == 1:
            return cases[0]
        raise ValueError("multiple GSLC stack cases are available; provide --case")
    for case in cases:
        if case.name == name:
            return case
    available = ", ".join(case.name for case in cases)
    raise ValueError(f"unknown GSLC stack case {name!r}; available: {available}")


__all__ = ["load_nisar_gslc_stack_config", "select_nisar_gslc_stack_case"]
