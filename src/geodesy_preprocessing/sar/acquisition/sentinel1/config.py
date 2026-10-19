"""Sentinel-1 查询、下载和 BURST 合并的严格项目配置。

功能概述:
    读取 ``[project]``、``[defaults]`` 和 ``[case:name]``，校验 AOI、日期、产品、
    轨道、方向、极化、执行边界和项目内路径，并在联网前拒绝任何凭据字段。

函数说明:
    ``load_sentinel1_acquisition_config`` 读取全部 case；
    ``select_sentinel1_acquisition_case`` 选择一个明确 case。
"""

from __future__ import annotations

import configparser
import math
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from geodesy_preprocessing.common.paths import validate_within_root

from .models import (
    Sentinel1AcquisitionCase,
    Sentinel1DownloadLayout,
    Sentinel1ProductType,
)


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SECRET_MARKERS = (
    "username",
    "password",
    "token",
    "cookie",
    "secret",
    "authorization",
    "credential",
)
_REQUIRED = {"start_date", "end_date", "query_dir", "download_dir"}
_ALLOWED = _REQUIRED | {
    "kml_file",
    "bbox",
    "provider",
    "product_type",
    "beam_mode",
    "polarizations",
    "tracks",
    "orbit_direction",
    "coverage_threshold",
    "download_threads",
    "max_retries",
    "retry_delay_seconds",
    "keep_burst_files",
    "download_layout",
}
_POLARIZATIONS = {"HH", "HV", "VH", "VV"}
_BURST_BEAMS = {"IW", "EW"}
_SCENE_BEAMS = _BURST_BEAMS | {"S1", "S2", "S3", "S4", "S5", "S6", "WV"}


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
    candidate = Path(value).expanduser()
    return validate_within_root(
        candidate if candidate.is_absolute() else root / candidate,
        root,
        label,
    )


def _parse_date(value: str, key: str, source: Path) -> date:
    if not _ISO_DATE.fullmatch(value):
        raise ValueError(f"{source}: {key} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be YYYY-MM-DD") from error


def _selection(value: str, source: Path) -> tuple[int, ...] | None:
    if value.strip().lower() == "auto":
        return None
    try:
        parsed = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as error:
        raise ValueError(f"{source}: tracks must be auto or positive integer list") from error
    if not parsed or any(item < 1 for item in parsed):
        raise ValueError(f"{source}: tracks must be auto or positive integer list")
    if len(set(parsed)) != len(parsed):
        raise ValueError(f"{source}: tracks contains a duplicate")
    return tuple(sorted(parsed))


def _polarizations(value: str, source: Path) -> tuple[str, ...] | None:
    if value.strip().lower() == "auto":
        return None
    parsed = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if not parsed or len(set(parsed)) != len(parsed):
        raise ValueError(f"{source}: polarizations must be unique or auto")
    unsupported = sorted(set(parsed) - _POLARIZATIONS)
    if unsupported:
        raise ValueError(f"{source}: unsupported polarization {unsupported[0]}")
    return parsed


def _bbox(value: str, source: Path) -> tuple[float, float, float, float]:
    try:
        parsed = tuple(float(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as error:
        raise ValueError(f"{source}: bbox must be west,south,east,north") from error
    if len(parsed) != 4 or not all(math.isfinite(item) for item in parsed):
        raise ValueError(f"{source}: bbox must be west,south,east,north")
    west, south, east, north = parsed
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError(f"{source}: bbox bounds are invalid")
    return west, south, east, north


def _integer(
    value: str, key: str, source: Path, minimum: int, maximum: int
) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{source}: {key} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{source}: {key} must be between {minimum} and {maximum}")
    return parsed


def _boolean(value: str, key: str, source: Path) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1", "on"}:
        return True
    if normalized in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"{source}: {key} must be true or false")


def load_sentinel1_acquisition_config(
    path: str | Path,
) -> tuple[Sentinel1AcquisitionCase, ...]:
    """Load ordered Sentinel-1 cases without resolving credentials or network data."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid Sentinel-1 configuration {source}: {error}") from error
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

    cases: list[Sentinel1AcquisitionCase] = []
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

        kml_text = values.get("kml_file", "").strip()
        bbox_text = values.get("bbox", "").strip()
        if bool(kml_text) == bool(bbox_text):
            raise ValueError(f"{source}: [{section_name}] requires exactly one of kml_file or bbox")
        start = _parse_date(values["start_date"], "start_date", source)
        end = _parse_date(values["end_date"], "end_date", source)
        if start > end:
            raise ValueError(f"{source}: start_date must not be after end_date")

        try:
            product_type = Sentinel1ProductType(values.get("product_type", "BURST").upper())
        except ValueError as error:
            raise ValueError(f"{source}: product_type must be BURST, SLC or GRD") from error
        beam_mode = values.get("beam_mode", "IW").upper()
        allowed_beams = _BURST_BEAMS if product_type is Sentinel1ProductType.BURST else _SCENE_BEAMS
        if beam_mode not in allowed_beams:
            raise ValueError(
                f"{source}: beam_mode {beam_mode} is unsupported for {product_type.value}"
            )
        provider = values.get("provider", "asf").lower()
        if provider != "asf":
            raise ValueError(f"{source}: provider must be asf")
        direction_text = values.get("orbit_direction", "auto").lower()
        if direction_text not in {"auto", "ascending", "descending"}:
            raise ValueError(f"{source}: orbit_direction must be auto, ascending or descending")
        try:
            threshold = float(values.get("coverage_threshold", "0.999"))
        except ValueError as error:
            raise ValueError(f"{source}: coverage_threshold must be numeric") from error
        if not math.isfinite(threshold) or not 0.0 < threshold <= 1.0:
            raise ValueError(f"{source}: coverage_threshold must be greater than 0 and at most 1")
        tracks = _selection(values.get("tracks", "auto"), source)
        try:
            download_layout = Sentinel1DownloadLayout(
                values.get("download_layout", "orbit_subdirectories").lower()
            )
        except ValueError as error:
            raise ValueError(
                f"{source}: download_layout must be orbit_subdirectories or flat"
            ) from error
        if download_layout is Sentinel1DownloadLayout.FLAT and (
            tracks is None or len(tracks) != 1
        ):
            raise ValueError(
                f"{source}: download_layout flat requires a single explicit track"
            )

        cases.append(
            Sentinel1AcquisitionCase(
                name=name,
                project_root=project_root,
                kml_file=_resolve(project_root, kml_text, "KML") if kml_text else None,
                bbox=_bbox(bbox_text, source) if bbox_text else None,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                product_type=product_type,
                beam_mode=beam_mode,
                polarizations=_polarizations(values.get("polarizations", "auto"), source),
                provider=provider,
                tracks=tracks,
                orbit_direction=None if direction_text == "auto" else direction_text.upper(),
                coverage_threshold=threshold,
                query_dir=_resolve(project_root, values["query_dir"], "query"),
                download_dir=_resolve(project_root, values["download_dir"], "download"),
                download_threads=_integer(
                    values.get("download_threads", "2"), "download_threads", source, 1, 32
                ),
                max_retries=_integer(
                    values.get("max_retries", "3"), "max_retries", source, 0, 20
                ),
                retry_delay_seconds=_integer(
                    values.get("retry_delay_seconds", "10"),
                    "retry_delay_seconds",
                    source,
                    0,
                    86400,
                ),
                keep_burst_files=_boolean(
                    values.get("keep_burst_files", "true"),
                    "keep_burst_files",
                    source,
                ),
                download_layout=download_layout,
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def select_sentinel1_acquisition_case(
    cases: Sequence[Sentinel1AcquisitionCase], name: str | None
) -> Sentinel1AcquisitionCase:
    """Select a named case or the sole unambiguous case."""

    if name is None:
        if len(cases) != 1:
            raise ValueError("--case is required when configuration has multiple cases")
        return cases[0]
    for case in cases:
        if case.name == name:
            return case
    raise ValueError(f"unknown Sentinel-1 case {name!r}")


__all__ = [
    "load_sentinel1_acquisition_config",
    "select_sentinel1_acquisition_case",
]
