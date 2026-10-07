"""Project-local configuration for GAMMA TOPS coregistration.

功能概述:
    读取严格项目 INI 配置，合并公共配准设置，解析具名轨道 case，并生成正式
    S1-05 CLI 参数。

函数说明:
    ``GammaTopsCoregCase``（兼容名 ``CoregConfigCase``）保存 case；``load_coreg_config`` 读取配置；
    ``select_coreg_case`` 选择 case；``coreg_case_arguments`` 生成 CLI 参数。
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_DEFAULT_KEYS = {
    "range_looks", "azimuth_looks", "coherence_threshold", "fraction_threshold",
    "phase_stdev_threshold", "id_mode", "npoly", "bridge_mode",
}
_CASE_KEYS = _DEFAULT_KEYS | {
    "slc_dir", "dem_geocode_dir", "output_dir", "reference", "images",
}
_REQUIRED = _CASE_KEYS


@dataclass(frozen=True)
class GammaTopsCoregCase:
    """One resolved project-specific S1-05 case."""

    name: str
    slc_dir: Path
    dem_geocode_dir: Path
    output_dir: Path
    reference: str
    images_file: Path
    range_looks: int
    azimuth_looks: int
    coherence_threshold: float
    fraction_threshold: float
    phase_stdev_threshold: float
    id_mode: str
    npoly: int
    bridge_mode: str


CoregConfigCase = GammaTopsCoregCase


def _reject_unknown(section: configparser.SectionProxy, allowed: set[str], source: Path) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ValueError(f"{source}: unknown key in [{section.name}]: {', '.join(unknown)}")


def _integer(value: str, label: str, source: Path) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{source}: {label} must be an integer") from error
    if parsed < 1:
        raise ValueError(f"{source}: {label} must be positive")
    return parsed


def _number(value: str, label: str, source: Path) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{source}: {label} must be numeric") from error
    if parsed <= 0:
        raise ValueError(f"{source}: {label} must be positive")
    return parsed


def load_coreg_config(path: str | Path) -> tuple[CoregConfigCase, ...]:
    """Load ordered S1-05 cases from one strict project-local configuration."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid coregistration configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    _reject_unknown(parser["project"], {"root"}, source)
    root_text = parser["project"].get("root", "").strip()
    if not root_text:
        raise ValueError(f"{source}: [project] root is required")
    root = (source.parent / root_text).resolve()
    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        _reject_unknown(parser["defaults"], _DEFAULT_KEYS, source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())
    cases: list[CoregConfigCase] = []
    for section_name in parser.sections():
        if section_name in {"project", "defaults"}:
            continue
        if not section_name.startswith("case:"):
            raise ValueError(f"{source}: unsupported section [{section_name}]")
        name = section_name.removeprefix("case:").strip()
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: unsafe or empty case name: {name!r}")
        section = parser[section_name]
        _reject_unknown(section, _CASE_KEYS, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        missing = sorted(key for key in _REQUIRED if not values.get(key))
        if missing:
            raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")
        bridge = values["bridge_mode"]
        if bridge not in {"none", "yearly", "adjacent", "mix"}:
            raise ValueError(f"{source}: bridge mode is invalid: {bridge}")
        id_mode = values["id_mode"]
        if id_mode not in {"date", "date_time", "date_pol", "date_time_pol"}:
            raise ValueError(f"{source}: ID mode is invalid: {id_mode}")
        npoly = _integer(values["npoly"], "npoly", source)
        if npoly not in {1, 3, 4, 6}:
            raise ValueError(f"{source}: npoly must be 1, 3, 4, or 6")

        def resolved(key: str) -> Path:
            value = Path(values[key]).expanduser()
            return (value if value.is_absolute() else root / value).resolve()

        cases.append(
            CoregConfigCase(
                name=name,
                slc_dir=resolved("slc_dir"),
                dem_geocode_dir=resolved("dem_geocode_dir"),
                output_dir=resolved("output_dir"),
                reference=values["reference"],
                images_file=resolved("images"),
                range_looks=_integer(values["range_looks"], "range looks", source),
                azimuth_looks=_integer(values["azimuth_looks"], "azimuth looks", source),
                coherence_threshold=_number(values["coherence_threshold"], "coherence threshold", source),
                fraction_threshold=_number(values["fraction_threshold"], "fraction threshold", source),
                phase_stdev_threshold=_number(values["phase_stdev_threshold"], "phase standard-deviation threshold", source),
                id_mode=id_mode,
                npoly=npoly,
                bridge_mode=bridge,
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def select_coreg_case(cases: Sequence[CoregConfigCase], name: str | None) -> CoregConfigCase:
    """Select one case, rejecting ambiguous or unknown selection."""

    if name is None:
        if len(cases) != 1:
            raise ValueError("--case is required when configuration has multiple cases")
        return cases[0]
    for case in cases:
        if case.name == name:
            return case
    raise ValueError(f"unknown coregistration case {name!r}")


def coreg_case_arguments(case: CoregConfigCase) -> tuple[str, ...]:
    """Return positional compatibility arguments plus explicit project settings."""

    return (
        str(case.slc_dir), str(case.dem_geocode_dir), str(case.output_dir),
        case.reference, str(case.images_file), str(case.range_looks),
        str(case.azimuth_looks), "--cc", str(case.coherence_threshold),
        "--fraction", str(case.fraction_threshold), "--ph-stdev",
        str(case.phase_stdev_threshold), "--id-mode", case.id_mode,
        "--npoly", str(case.npoly), "--bridge-mode", case.bridge_mode,
    )


__all__ = ["GammaTopsCoregCase", "CoregConfigCase", "coreg_case_arguments", "load_coreg_config", "select_coreg_case"]
