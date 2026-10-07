"""Project configuration for standalone GAMMA DEM conversion.

功能概述:
    读取严格的分节 CFG，将项目相对路径转换成一个不可变的 GAMMA DEM 任务。

函数说明:
    ``GammaDemConvertCase`` 保存已解析任务；
    ``load_gamma_dem_convert_config`` 读取并校验唯一的具名 case。
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_PROJECT_KEYS = {"root"}
_CASE_KEYS = {
    "input_geotiff",
    "output_dem",
    "output_par",
    "log_file",
    "command",
    "geoid_correction_flag",
    "geoid_file",
    "gdal_bin_dir",
    "overwrite",
}
_REQUIRED_KEYS = {"input_geotiff", "output_dem", "output_par"}


@dataclass(frozen=True)
class GammaDemConvertCase:
    """One resolved project-local GAMMA ``srtm2dem`` conversion task."""

    name: str
    input_geotiff: Path
    output_dem: Path
    output_par: Path
    log_path: Path | None
    command: str
    geoid_correction_flag: int
    geoid_path: Path | None
    gdal_bin_dir: Path | None
    overwrite: bool


def _reject_unknown(
    section: configparser.SectionProxy, allowed: set[str], source: Path
) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ValueError(
            f"{source}: unknown key in [{section.name}]: {', '.join(unknown)}"
        )


def _resolve(project_root: Path, value: str) -> Path:
    candidate = Path(value.strip()).expanduser()
    if not str(candidate):
        raise ValueError("path value cannot be empty")
    return (candidate if candidate.is_absolute() else project_root / candidate).absolute()


def _boolean(value: str, key: str, source: Path) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{source}: {key} must be true or false")


def load_gamma_dem_convert_config(path: str | Path) -> GammaDemConvertCase:
    """Load exactly one strict ``[case:name]`` GAMMA DEM conversion task."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid GAMMA DEM configuration {source}: {error}") from error

    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    project = parser["project"]
    _reject_unknown(project, _PROJECT_KEYS, source)
    root_value = project.get("root", "").strip()
    if not root_value:
        raise ValueError(f"{source}: [project] root is required")
    project_root = _resolve(source.parent, root_value).resolve()

    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        _reject_unknown(parser["defaults"], _CASE_KEYS, source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())

    unsupported = [
        name
        for name in parser.sections()
        if name not in {"project", "defaults"} and not name.startswith("case:")
    ]
    if unsupported:
        raise ValueError(f"{source}: unsupported section [{unsupported[0]}]")
    case_sections = [name for name in parser.sections() if name.startswith("case:")]
    if len(case_sections) != 1:
        raise ValueError(f"{source}: exactly one [case:name] section is required")

    section_name = case_sections[0]
    name = section_name.removeprefix("case:").strip()
    if not _CASE_NAME.fullmatch(name):
        raise ValueError(f"{source}: unsafe or empty GAMMA DEM case name: {name!r}")
    section = parser[section_name]
    _reject_unknown(section, _CASE_KEYS, source)
    values = dict(defaults)
    values.update((key, value.strip()) for key, value in section.items())
    missing = sorted(key for key in _REQUIRED_KEYS if not values.get(key))
    if missing:
        raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")

    command = values.get("command", "srtm2dem").strip()
    if not command:
        raise ValueError(f"{source}: command cannot be empty")
    try:
        geoid_flag = int(values.get("geoid_correction_flag", "0"))
    except ValueError as error:
        raise ValueError(f"{source}: geoid_correction_flag must be an integer") from error
    if geoid_flag not in {0, 1, 2, 3}:
        raise ValueError(f"{source}: geoid_correction_flag must be 0, 1, 2, or 3")
    geoid_value = values.get("geoid_file", "").strip()
    if geoid_flag == 2 and not geoid_value:
        raise ValueError(f"{source}: geoid correction flag 2 requires geoid_file")
    gdal_value = values.get("gdal_bin_dir", "").strip()
    log_value = values.get("log_file", "").strip()
    return GammaDemConvertCase(
        name=name,
        input_geotiff=_resolve(project_root, values["input_geotiff"]),
        output_dem=_resolve(project_root, values["output_dem"]),
        output_par=_resolve(project_root, values["output_par"]),
        log_path=_resolve(project_root, log_value) if log_value else None,
        command=command,
        geoid_correction_flag=geoid_flag,
        geoid_path=_resolve(project_root, geoid_value) if geoid_value else None,
        gdal_bin_dir=_resolve(project_root, gdal_value) if gdal_value else None,
        overwrite=_boolean(values.get("overwrite", "false"), "overwrite", source),
    )


__all__ = ["GammaDemConvertCase", "load_gamma_dem_convert_config"]
