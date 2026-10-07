"""历史 S1-01 参数文件到正式 Sentinel-1 获取入口的薄适配器。

功能概述:
    只读取旧 key=value 文件中的非敏感任务参数，生成一次性严格 CFG 并委托正式命令；
    旧文件内的账号密码永不复制或使用，查询后的 ``images_NNN`` 仍复制到旧输出根目录。

函数说明:
    ``legacy_main`` 保留历史 ``CONFIG --query|--download|--merge`` 调用形式。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .cli import main as canonical_main


_SECRET_KEYS = {
    "earthdata_username",
    "earthdata_password",
    "username",
    "password",
    "token",
    "cookie",
}
_PRODUCTS = {"S1_Bursts": "BURST", "S1_SLC": "SLC", "S1_GRD": "GRD"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="S1-01download.py",
        description="Deprecated adapter; use geodesy-pre-sar-sentinel1-acquire.",
    )
    parser.add_argument("config", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--query", "--search", dest="query", action="store_true")
    action.add_argument("--download", action="store_true")
    action.add_argument("--merge", action="store_true")
    return parser


def _read(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "%")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.lower() in _SECRET_KEYS:
            continue
        values[key] = os.path.expandvars(os.path.expanduser(value.split("#", 1)[0].strip()))
    return values


def _date(value: str, label: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as error:
        raise ValueError(f"legacy {label} must use YYYYMMDD") from error


def _path(base: Path, value: str) -> Path:
    candidate = Path(value)
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _integer(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _canonical_document(source: Path, values: dict[str, str]) -> tuple[str, Path, Path]:
    base = source.parent
    if not values.get("output_dir"):
        raise ValueError("legacy config is missing output_dir")
    output = _path(base, values["output_dir"])
    aoi_value = values.get("AOI_File", "NONE").strip()
    aoi = None if not aoi_value or aoi_value.upper() == "NONE" else _path(base, aoi_value)
    roots = [base.resolve(), output]
    if aoi is not None:
        roots.append(aoi.parent)
    project_root = Path(os.path.commonpath(tuple(str(item) for item in roots)))
    old_product = values.get("S1_product_type", "S1_Bursts")
    if old_product not in _PRODUCTS:
        raise ValueError(f"unsupported legacy S1_product_type {old_product}")
    tracks_value = values.get("relOrbit", "Auto").strip()
    if tracks_value.lower() == "auto":
        tracks = "auto"
    else:
        try:
            tracks = ",".join(str(item) for item in sorted({int(part.strip()) for part in tracks_value.replace(";", ",").split(",") if part.strip()}))
        except ValueError as error:
            raise ValueError("legacy relOrbit must be Auto or integer list") from error
    direction = values.get("flight_direction", "Auto").strip().lower()
    if direction not in {"auto", "ascending", "descending"}:
        raise ValueError("legacy flight_direction must be Auto, Ascending or Descending")
    polarization = values.get("S1_polarization", "Auto").strip()
    aoi_line = (
        f"kml_file = {aoi}"
        if aoi is not None
        else "bbox = " + ",".join(
            values.get(name, "") for name in ("LONMIN", "LATMIN", "LONMAX", "LATMAX")
        )
    )
    document = f"""[project]
root = {project_root}

[case:legacy]
{aoi_line}
start_date = {_date(values.get('Start_time', ''), 'Start_time')}
end_date = {_date(values.get('End_time', ''), 'End_time')}
provider = asf
product_type = {_PRODUCTS[old_product]}
beam_mode = {values.get('S1_beam_mode', 'IW').upper()}
polarizations = {polarization}
tracks = {tracks}
orbit_direction = {direction}
coverage_threshold = 0.999
query_dir = {output / 'QUERY'}
download_dir = {output}
download_threads = {_integer(values.get('download_threads', '1'), 1, 1, 32)}
max_retries = {_integer(values.get('max_tries', '3'), 3, 0, 20)}
retry_delay_seconds = {_integer(values.get('time_sleep', '10'), 10, 0, 86400)}
keep_burst_files = {values.get('keep_burst_tif', 'True').lower()}
"""
    return document, output, output / "QUERY"


def legacy_main(arguments: Sequence[str] | None = None) -> int:
    """Translate one old configuration and delegate to the canonical command."""

    options = _parser().parse_args(arguments)
    print(
        "DEPRECATED: S1-01download.py now delegates to "
        "geodesy-pre-sar-sentinel1-acquire; credentials in the old CFG are ignored.",
        file=sys.stderr,
    )
    try:
        source = options.config.expanduser().resolve(strict=True)
        document, output, query_dir = _canonical_document(source, _read(source))
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    action = "--download" if options.download else "--merge" if options.merge else "--query"
    descriptor, name = tempfile.mkstemp(prefix=".sentinel1-legacy-", suffix=".cfg", dir=source.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(document)
        code = canonical_main(("--config", str(temporary), "--case", "legacy", action))
        if code == 0 and action == "--query" and query_dir.is_dir():
            output.mkdir(parents=True, exist_ok=True)
            for image_list in query_dir.glob("images_[0-9][0-9][0-9]"):
                shutil.copy2(image_list, output / image_list.name)
        return code
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["legacy_main"]
