"""Compatibility entry points for historical GAMMA radar-grid SLC scripts.

功能概述:
    把六个旧脚本的位置参数转换成经过验证的 ``OtherSarSlcCase``，实际发现、转换、
    日志和发布仍由统一 Python 工作流完成。旧入口不再保存独立处理逻辑。

函数说明:
    ``legacy_slc_case`` 翻译旧参数；``legacy_slc_main`` 执行一个卫星入口；
    ``main`` 为所有兼容壳层提供统一模块命令。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType

from .adapters.saocom import saocom_polarizations_for_mode
from .config import OtherSarSlcCase
from .cli import _LocalProgress
from .plan import build_slc_plan
from .runner import OtherSarCommandError, run_slc_batch

_SCRIPT_NAMES = {
    "alos2": "01-raw2slc_ALOS2",
    "csg": "01-raw2slc_CSG",
    "csks": "01-raw2slc_CSKS",
    "lt1": "01-raw2slc_LT1",
    "rsat2": "01-raw2slc_RSAT2",
    "saocom": "01-raw2slc_SAOCOM",
}
_LOOKS = {
    "alos2": (10, 10),
    "csg": (10, 10),
    "csks": (10, 10),
    "lt1": (10, 20),
    "rsat2": (20, 10),
    "saocom": (10, 20),
}
_IMAGE_NAMES = {
    "alos2": "images",
    "csg": "images_CSG",
    "csks": "images_CSKS",
    "lt1": "images_LT1",
    "rsat2": "images",
    "saocom": "images",
}


def _usage(sensor: str) -> str:
    script = _SCRIPT_NAMES[sensor]
    suffix = " <pol>" if sensor == "saocom" else ""
    return f"usage: {script} <raw_input_path> <slc_output_path>{suffix}"


def legacy_slc_case(
    sensor: str,
    arguments: Sequence[str],
    working_directory: str | Path | None = None,
) -> OtherSarSlcCase:
    """Translate one historical positional invocation into a validated case."""

    normalized = sensor.lower()
    if normalized not in _SCRIPT_NAMES:
        raise ValueError(f"unsupported historical Other-SAR sensor: {sensor}")
    required = 3 if normalized == "saocom" else 2
    if len(arguments) != required:
        raise ValueError(_usage(normalized))
    cwd = Path.cwd() if working_directory is None else Path(working_directory)
    cwd = cwd.expanduser().resolve()
    raw_dir = Path(arguments[0]).expanduser().resolve()
    slc_dir = Path(arguments[1]).expanduser().resolve()
    if normalized == "alos2":
        polarizations: tuple[str, ...] = ()
    elif normalized == "saocom":
        polarizations = saocom_polarizations_for_mode(arguments[2])
    else:
        polarizations = ("HH",)
    range_looks, azimuth_looks = _LOOKS[normalized]
    return OtherSarSlcCase(
        name=f"legacy_{normalized}",
        sensor=normalized,
        raw_dir=raw_dir,
        slc_dir=slc_dir,
        images_file=cwd / _IMAGE_NAMES[normalized],
        polarizations=polarizations,
        range_looks=range_looks,
        azimuth_looks=azimuth_looks,
        commands=MappingProxyType({}),
        workflow_task="PRE_OTHER_SAR_SLC",
    )


def legacy_slc_main(sensor: str, arguments: Sequence[str] | None = None) -> int:
    """Run one historical SLC interface and return a conventional exit code."""

    values = list(sys.argv[1:] if arguments is None else arguments)
    normalized = sensor.lower()
    if normalized not in _SCRIPT_NAMES:
        print(f"ERROR: unsupported historical Other-SAR sensor: {sensor}", file=sys.stderr)
        return 2
    if not values:
        print(_usage(normalized))
        return 0
    try:
        case = legacy_slc_case(normalized, values)
        plan = build_slc_plan(case)
        recorded = os.environ.get("GEODESY_PROGRESS_PROTOCOL") == "1"
        sink = print if recorded else _LocalProgress(tty=sys.stdout.isatty())
        run_slc_batch(plan, progress_sink=sink)
    except KeyboardInterrupt:
        print("ERROR: Other-SAR SLC generation interrupted", file=sys.stderr)
        return 130
    except OtherSarCommandError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Dispatch ``slc <sensor>`` for the historical shell wrappers."""

    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) < 2 or values[0] != "slc":
        print(
            "usage: python -m "
            "geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.legacy "
            "slc <sensor> [args]"
        )
        return 2
    return legacy_slc_main(values[1], values[2:])


__all__ = ["legacy_slc_case", "legacy_slc_main", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
