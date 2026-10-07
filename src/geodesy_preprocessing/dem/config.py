"""Strict sectionless configuration for DEM preparation.

功能概述:
    读取 ``key=value`` 配置、拒绝重复或未知字段，并按配置文件目录解析路径。

函数说明:
    ``load_config`` 读取配置；``validate_config_keys`` 检查字段；
    ``cfg_str``、``cfg_float``、``cfg_int``、``cfg_bool`` 提供类型读取；
    ``resolve_path`` 解析相对路径。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path


ALLOWED_CONFIG_KEYS = frozenset(
    {
        "input_mode",
        "kml_file",
        "kml_placemark",
        "west",
        "south",
        "east",
        "north",
        "buffer_km",
        "dem_type",
        "api_url",
        "api_key",
        "api_key_env",
        "output_format",
        "output_dir",
        "output_name",
        "write_metadata",
        "metadata_name",
        "overwrite",
        "timeout_connect_sec",
        "timeout_read_sec",
        "retries",
        "backoff_factor",
        "chunk_mb",
        "verify_tiff",
        "source_vertical_datum",
        "run_srtm2dem",
        "srtm2dem_command",
        "srtm2dem_dem_name",
        "srtm2dem_par_name",
        "srtm2dem_log_name",
        "srtm2dem_geoid_correction_flag",
        "srtm2dem_geoid_file",
        "srtm2dem_gdal_bin_dir",
        "dry_run",
    }
)


def load_config(path: str | Path) -> dict[str, str]:
    """Read one strict, sectionless UTF-8 ``key=value`` file."""

    source = Path(path).expanduser().resolve(strict=True)
    values: dict[str, str] = {}
    with source.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", ";", "%")):
                continue
            if "=" not in line:
                raise ValueError(
                    f"{source}:{line_number}: expected key=value configuration"
                )
            raw_key, raw_value = line.split("=", 1)
            key = raw_key.strip().lower()
            if not key:
                raise ValueError(f"{source}:{line_number}: configuration key is empty")
            if key in values:
                raise ValueError(
                    f"{source}:{line_number}: duplicate configuration key {key!r}"
                )
            value = raw_value.split("#", 1)[0].strip()
            values[key] = os.path.expandvars(os.path.expanduser(value))
    return values


def validate_config_keys(
    cfg: Mapping[str, str],
    allowed_keys: Iterable[str],
    source: str | Path = "<config>",
) -> None:
    """Reject configuration fields that are not explicitly supported."""

    unknown = sorted(set(cfg) - set(allowed_keys))
    if unknown:
        raise ValueError(f"{source}: unknown configuration keys: {', '.join(unknown)}")


def cfg_str(cfg: Mapping[str, str], key: str, default: str = "") -> str:
    """Read one trimmed string value."""

    return str(cfg.get(key.lower(), default)).strip()


def cfg_float(
    cfg: Mapping[str, str], key: str, default: float | None = None
) -> float | None:
    """Read one optional floating-point value."""

    raw = cfg_str(cfg, key)
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a number, got {raw!r}") from error


def cfg_int(
    cfg: Mapping[str, str], key: str, default: int | None = None
) -> int | None:
    """Read one optional integer value."""

    raw = cfg_str(cfg, key)
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from error


def cfg_bool(cfg: Mapping[str, str], key: str, default: bool = False) -> bool:
    """Read one boolean using common human-readable spellings."""

    raw = cfg_str(cfg, key)
    if raw == "":
        return default
    normalized = raw.lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean, got {raw!r}")


def resolve_path(value: str, base_dir: Path) -> Path:
    """Resolve a non-empty path relative to the configuration directory."""

    text = os.path.expandvars(os.path.expanduser(value.strip()))
    if not text:
        raise ValueError("path value cannot be empty")
    candidate = Path(text)
    return (candidate if candidate.is_absolute() else base_dir / candidate).resolve()


__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "cfg_bool",
    "cfg_float",
    "cfg_int",
    "cfg_str",
    "load_config",
    "resolve_path",
    "validate_config_keys",
]

