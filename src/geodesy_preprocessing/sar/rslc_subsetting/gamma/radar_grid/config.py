"""Project configuration for KML-driven GAMMA registered-stack cropping.

功能概述:
    读取严格 INI 配置，解析项目路径、缓冲距离、极化选择和覆盖率要求。

函数说明:
    ``GammaRadarCropConfig`` 保存一个已解析 case；
    ``load_crop_config`` 读取并选择具名裁剪 case。
"""

from __future__ import annotations

import configparser
import math
import re
from dataclasses import dataclass
from pathlib import Path


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_POLARIZATION = re.compile(r"[A-Z0-9]+\Z")
_DEFAULT_KEYS = {
    "buffer_km",
    "polarizations",
    "require_full_aoi_coverage",
    "min_buffer_coverage",
}
_CASE_KEYS = _DEFAULT_KEYS | {
    "registered_stack",
    "geometry_dir",
    "kml_file",
    "kml_placemark",
    "output_dir",
}
_REQUIRED = {
    "registered_stack",
    "geometry_dir",
    "kml_file",
    "output_dir",
    "buffer_km",
    "polarizations",
    "require_full_aoi_coverage",
    "min_buffer_coverage",
}


@dataclass(frozen=True)
class GammaRadarCropConfig:
    """One resolved project-specific registered-stack crop case."""

    name: str
    project_root: Path
    registered_stack: Path
    geometry_dir: Path
    kml_file: Path
    kml_placemark: str | None
    output_dir: Path
    buffer_km: float
    polarizations: tuple[str, ...] | None
    require_full_aoi_coverage: bool
    min_buffer_coverage: float


def _reject_unknown(
    section: configparser.SectionProxy,
    allowed: set[str],
    source: Path,
) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ValueError(f"{source}: unknown key in [{section.name}]: {', '.join(unknown)}")


def _number(value: str, field: str, source: Path) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{source}: {field} must be numeric") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{source}: {field} must be finite")
    return parsed


def _boolean(value: str, field: str, source: Path) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1", "on"}:
        return True
    if normalized in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"{source}: {field} must be true or false")


def _polarizations(value: str, source: Path) -> tuple[str, ...] | None:
    normalized = value.strip().upper()
    if normalized == "ALL":
        return None
    values = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{source}: polarizations must be unique labels or all")
    if any(not _POLARIZATION.fullmatch(item) for item in values):
        raise ValueError(f"{source}: invalid polarization label")
    return values


def load_crop_config(path: Path, case: str) -> GammaRadarCropConfig:
    """Load and resolve one named crop case from a strict project-local CFG."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid crop configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    _reject_unknown(parser["project"], {"root"}, source)
    root_text = parser["project"].get("root", "").strip()
    if not root_text:
        raise ValueError(f"{source}: [project] root is required")
    project_root = (source.parent / root_text).resolve()
    defaults: dict[str, str] = {"buffer_km": "5"}
    if parser.has_section("defaults"):
        _reject_unknown(parser["defaults"], _DEFAULT_KEYS, source)
        defaults.update((key, value.strip()) for key, value in parser["defaults"].items())
    section_name = f"case:{case}"
    if not _CASE_NAME.fullmatch(case) or not parser.has_section(section_name):
        available = ", ".join(
            section.removeprefix("case:")
            for section in parser.sections()
            if section.startswith("case:")
        )
        raise ValueError(f"unknown crop case {case!r}; available: {available}")
    section = parser[section_name]
    _reject_unknown(section, _CASE_KEYS, source)
    values = dict(defaults)
    values.update((key, value.strip()) for key, value in section.items())
    missing = sorted(key for key in _REQUIRED if not values.get(key))
    if missing:
        raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")

    buffer_km = _number(values["buffer_km"], "buffer_km", source)
    if buffer_km < 0.0:
        raise ValueError(f"{source}: buffer_km must be non-negative")
    minimum = _number(values["min_buffer_coverage"], "min_buffer_coverage", source)
    if not 0.0 < minimum <= 1.0:
        raise ValueError(f"{source}: min_buffer_coverage must be greater than 0 and at most 1")

    def resolved(key: str) -> Path:
        value = Path(values[key]).expanduser()
        return (value if value.is_absolute() else project_root / value).resolve()

    placemark = values.get("kml_placemark", "").strip() or None
    return GammaRadarCropConfig(
        name=case,
        project_root=project_root,
        registered_stack=resolved("registered_stack"),
        geometry_dir=resolved("geometry_dir"),
        kml_file=resolved("kml_file"),
        kml_placemark=placemark,
        output_dir=resolved("output_dir"),
        buffer_km=buffer_km,
        polarizations=_polarizations(values["polarizations"], source),
        require_full_aoi_coverage=_boolean(
            values["require_full_aoi_coverage"],
            "require_full_aoi_coverage",
            source,
        ),
        min_buffer_coverage=minimum,
    )


__all__ = ["GammaRadarCropConfig", "load_crop_config"]
