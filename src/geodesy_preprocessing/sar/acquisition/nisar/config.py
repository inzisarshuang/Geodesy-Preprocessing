"""NISAR 查询与下载的严格项目配置。

功能概述:
    读取 ``[project]``、``[defaults]`` 与 ``[case:name]``，校验日期、轨道、帧、
    频率模式和项目内路径，并拒绝任何凭据字段。

函数说明:
    ``load_nisar_config`` 读取全部不可变 case；``select_nisar_case`` 选择一个 case。
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from geodesy_preprocessing.common.paths import validate_within_root

from .models import NisarFrequencyMode, NisarProductType, NisarSearchCase


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SECRET_MARKERS = ("password", "token", "cookie", "secret", "authorization")
_REQUIRED = {"kml_file", "start_date", "end_date", "polarizations"}
_ALLOWED = _REQUIRED | {
    "product_type",
    "band",
    "provider",
    "frequency_mode",
    "tracks",
    "frames",
    "orbit_direction",
    "query_dir",
    "download_dir",
}
_POLARIZATIONS = {"HH", "HV", "VH", "VV"}


def _check_keys(section: configparser.SectionProxy, allowed: set[str], source: Path) -> None:
    keys = set(section)
    credential = sorted(
        key for key in keys if any(marker in key.lower() for marker in _SECRET_MARKERS)
    )
    if credential:
        raise ValueError(f"{source}: credential field is forbidden: {credential[0]}")
    unknown = sorted(keys - allowed)
    if unknown:
        raise ValueError(f"{source}: unknown key in [{section.name}]: {unknown[0]}")


def _resolve(root: Path, value: str, label: str) -> Path:
    path = Path(value).expanduser()
    return validate_within_root(path if path.is_absolute() else root / path, root, label)


def _date(value: str, key: str, source: Path) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be YYYY-MM-DD") from error


def _selection(value: str, key: str, source: Path) -> tuple[int, ...] | None:
    if value.strip().lower() == "auto":
        return None
    try:
        parsed = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be auto or positive integer list") from error
    if not parsed or any(item < 1 for item in parsed):
        raise ValueError(f"{source}: {key} must be auto or positive integer list")
    if len(set(parsed)) != len(parsed):
        raise ValueError(f"{source}: {key} contains a duplicate")
    return tuple(sorted(parsed))


def _polarizations(value: str, source: Path) -> tuple[str, ...]:
    values = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{source}: polarizations must be unique")
    unsupported = sorted(set(values) - _POLARIZATIONS)
    if unsupported:
        raise ValueError(f"{source}: unsupported polarization {unsupported[0]}")
    return values


def load_nisar_config(path: str | Path) -> tuple[NisarSearchCase, ...]:
    """Load ordered NISAR acquisition cases without resolving credentials."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid NISAR configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    _check_keys(parser["project"], {"root"}, source)
    root_text = parser["project"].get("root", "").strip()
    if not root_text:
        raise ValueError(f"{source}: [project] root is required")
    raw_root = Path(root_text).expanduser()
    project_root = (raw_root if raw_root.is_absolute() else source.parent / raw_root).resolve()
    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        _check_keys(parser["defaults"], _ALLOWED, source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())
    cases: list[NisarSearchCase] = []
    for section_name in parser.sections():
        if section_name in {"project", "defaults"}:
            continue
        if not section_name.startswith("case:"):
            raise ValueError(f"{source}: unsupported section [{section_name}]")
        name = section_name.removeprefix("case:").strip()
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: unsafe or empty case name: {name!r}")
        section = parser[section_name]
        _check_keys(section, _ALLOWED, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        missing = sorted(key for key in _REQUIRED if not values.get(key))
        if missing:
            raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")
        start = _date(values["start_date"], "start_date", source)
        end = _date(values["end_date"], "end_date", source)
        if start > end:
            raise ValueError(f"{source}: start_date must not be after end_date")
        try:
            product_type = NisarProductType(values.get("product_type", "RSLC").upper())
        except ValueError as error:
            raise ValueError(f"{source}: product_type must be RSLC or GSLC") from error
        band = values.get("band", "L").upper()
        if band not in {"L", "S"}:
            raise ValueError(f"{source}: band must be L or S")
        provider = values.get("provider", "asf").lower()
        if provider != "asf":
            raise ValueError(f"{source}: provider must be asf")
        try:
            frequency_mode = NisarFrequencyMode(
                values.get("frequency_mode", "main_only").lower()
            )
        except ValueError as error:
            raise ValueError(
                f"{source}: frequency_mode must be main_only or main_plus_auxiliary"
            ) from error
        direction_text = values.get("orbit_direction", "auto").lower()
        if direction_text not in {"auto", "ascending", "descending"}:
            raise ValueError(f"{source}: orbit_direction must be auto, ascending or descending")
        cases.append(
            NisarSearchCase(
                name=name,
                project_root=project_root,
                kml_file=_resolve(project_root, values["kml_file"], "KML"),
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                product_type=product_type,
                band=band,
                polarizations=_polarizations(values["polarizations"], source),
                provider=provider,
                frequency_mode=frequency_mode,
                tracks=_selection(values.get("tracks", "auto"), "tracks", source),
                frames=_selection(values.get("frames", "auto"), "frames", source),
                orbit_direction=None if direction_text == "auto" else direction_text.upper(),
                query_dir=_resolve(project_root, values.get("query_dir", "QUERY/NISAR"), "query"),
                download_dir=_resolve(project_root, values.get("download_dir", "INPUT/NISAR"), "download"),
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def select_nisar_case(
    cases: Sequence[NisarSearchCase], name: str | None
) -> NisarSearchCase:
    """Select a named case or the sole unambiguous case."""

    if name is None:
        if len(cases) != 1:
            raise ValueError("--case is required when configuration has multiple cases")
        return cases[0]
    for case in cases:
        if case.name == name:
            return case
    raise ValueError(f"unknown NISAR case {name!r}")


__all__ = ["load_nisar_config", "select_nisar_case"]
