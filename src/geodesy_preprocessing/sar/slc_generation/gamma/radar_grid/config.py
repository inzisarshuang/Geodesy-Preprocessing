"""Strict project configuration for GAMMA radar-grid SLC generation.

功能概述:
    读取 ``[project]``、``[defaults]`` 和 ``[case:name]`` 配置，解析项目内路径，
    规范卫星与极化名称，并拒绝未知、危险或含 shell 操作符的值。

函数说明:
    ``GammaRadarSlcCase`` 保存不可变 case；``load_gamma_radar_slc_config``
    读取新配置；``load_other_sar_slc_config`` 保留旧任务名；
    ``select_gamma_radar_slc_case`` 与 ``select_other_sar_slc_case`` 选择 case；
    ``validate_safe_date`` 校验日期；``validate_within_root`` 阻止路径逃逸。
"""

from __future__ import annotations

import configparser
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from geodesy_preprocessing.common.case_layout import optional_case_layout
from geodesy_preprocessing.common.paths import validate_within_root

_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_LEGACY_SENSORS = ("alos2", "csg", "csks", "lt1", "rsat2", "saocom")
_GAMMA_SENSORS = _LEGACY_SENSORS + ("nisar",)
_POLARIZATIONS = {"HH", "HV", "VH", "VV"}
_COMMAND_KEYS = {
    "import_command",
    "orbit_filter_command",
    "multi_look_command",
    "preview_command",
}
_VALUE_KEYS = {
    "polarizations",
    "range_looks",
    "azimuth_looks",
    "images_file",
    "workflow_task",
} | _COMMAND_KEYS
_ADAPTER_KEYS = {
    "adapter.band",
    "adapter.frequency",
    "adapter.radiometric_calibration",
    "adapter.noise_subtraction",
}
_CASE_KEYS = {
    "case_root",
    "sensor",
    "raw_dir",
    "slc_dir",
} | _VALUE_KEYS | _ADAPTER_KEYS
_SHELL_OPERATORS = re.compile(r"[;&|<>`\n\r]|\$\(")


@dataclass(frozen=True)
class GammaRadarSlcCase:
    """One resolved and validated vendor-to-SLC project case."""

    name: str
    sensor: str
    raw_dir: Path
    slc_dir: Path
    images_file: Path
    polarizations: tuple[str, ...]
    range_looks: int
    azimuth_looks: int
    commands: Mapping[str, str]
    workflow_task: str
    adapter_options: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    case_root: Path | None = None


OtherSarSlcCase = GammaRadarSlcCase


def _reject_unknown(
    section: configparser.SectionProxy, allowed: set[str], source: Path
) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ValueError(
            f"{source}: unknown key in [{section.name}]: {', '.join(unknown)}"
        )


def _positive_integer(value: str, name: str, source: Path) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{source}: {name} must be a positive integer") from error
    if parsed < 1:
        raise ValueError(f"{source}: {name} must be a positive integer")
    return parsed


def _command_value(value: str, name: str, source: Path) -> str:
    if _SHELL_OPERATORS.search(value):
        raise ValueError(f"{source}: {name} contains a shell operator")
    try:
        words = shlex.split(value)
    except ValueError as error:
        raise ValueError(f"{source}: {name} is not a valid command") from error
    if not words:
        raise ValueError(f"{source}: {name} must not be empty")
    return value


def validate_safe_date(value: str) -> str:
    """Return an eight-digit real calendar date or raise ``ValueError``."""

    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"unsafe acquisition date: {value!r}")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise ValueError(f"invalid acquisition date: {value!r}") from error
    return value


def _resolve_project_path(root: Path, value: str, label: str) -> Path:
    path = Path(value).expanduser()
    candidate = path if path.is_absolute() else root / path
    return validate_within_root(candidate, root, label)


def _polarizations(value: str, source: Path) -> tuple[str, ...]:
    parsed = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if not parsed:
        raise ValueError(f"{source}: polarizations must not be empty")
    if len(set(parsed)) != len(parsed):
        raise ValueError(f"{source}: duplicate polarization")
    unsupported = [value for value in parsed if value not in _POLARIZATIONS]
    if unsupported:
        raise ValueError(f"{source}: unsupported polarization: {unsupported[0]}")
    return parsed


def _load_sar_slc_config(
    path: str | Path, *, default_workflow_task: str, sensors: tuple[str, ...]
) -> tuple[GammaRadarSlcCase, ...]:

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid Other-SAR configuration {source}: {error}") from error
    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    project = parser["project"]
    _reject_unknown(project, {"root"}, source)
    if not project.get("root", "").strip():
        raise ValueError(f"{source}: [project] root is required")
    root_value = Path(project["root"].strip()).expanduser()
    project_root = (
        root_value if root_value.is_absolute() else source.parent / root_value
    ).resolve()

    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        section = parser["defaults"]
        _reject_unknown(section, _VALUE_KEYS, source)
        defaults.update((key, value.strip()) for key, value in section.items())
    invalid = [
        name
        for name in parser.sections()
        if name not in {"project", "defaults"} and not name.startswith("case:")
    ]
    if invalid:
        raise ValueError(f"{source}: unsupported section [{invalid[0]}]")

    cases: list[GammaRadarSlcCase] = []
    for section_name in parser.sections():
        if not section_name.startswith("case:"):
            continue
        name = section_name.removeprefix("case:").strip()
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: unsafe or empty Other-SAR case name: {name!r}")
        section = parser[section_name]
        _reject_unknown(section, _CASE_KEYS, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        missing = [key for key in ("sensor", "raw_dir", "slc_dir") if not values.get(key)]
        if missing:
            raise ValueError(f"{source}: [{section_name}] missing keys: {', '.join(missing)}")
        sensor = values["sensor"].lower()
        if sensor not in sensors:
            raise ValueError(f"{source}: sensor must be one of: {', '.join(sensors)}")
        provided_adapter_keys = sorted(set(values) & _ADAPTER_KEYS)
        if sensor != "nisar" and provided_adapter_keys:
            raise ValueError(f"{source}: adapter.* keys are only valid for sensor=nisar")
        adapter_options: dict[str, str] = {}
        if sensor == "nisar":
            band = values.get("adapter.band", "L").upper()
            frequency = values.get("adapter.frequency", "A").upper()
            calibration_name = values.get(
                "adapter.radiometric_calibration", "beta0"
            ).lower()
            noise_name = values.get("adapter.noise_subtraction", "false").lower()
            calibrations = {"none": "0", "beta0": "1", "sigma0": "2", "gamma0": "3"}
            booleans = {"false": "0", "true": "1"}
            if band not in {"L", "S"}:
                raise ValueError(f"{source}: adapter.band must be L or S")
            if frequency not in {"A", "B"}:
                raise ValueError(f"{source}: adapter.frequency must be A or B")
            if calibration_name not in calibrations:
                raise ValueError(
                    f"{source}: adapter.radiometric_calibration must be none, beta0, sigma0 or gamma0"
                )
            if noise_name not in booleans:
                raise ValueError(f"{source}: adapter.noise_subtraction must be true or false")
            adapter_options = {
                "band": band,
                "frequency": frequency,
                "radiometric_calibration": calibrations[calibration_name],
                "noise_subtraction": booleans[noise_name],
            }
        command_values = {
            key: _command_value(values[key], key, source)
            for key in _COMMAND_KEYS
            if values.get(key)
        }
        images_value = values.get("images_file", f"images_{sensor}")
        raw_dir = _resolve_project_path(project_root, values["raw_dir"], "raw")
        slc_dir = _resolve_project_path(project_root, values["slc_dir"], "SLC")
        images_file = _resolve_project_path(project_root, images_value, "images")
        layout = optional_case_layout(project_root, values)
        if layout is not None:
            slc_dir = layout.require_product(slc_dir, "SLC", "slc_dir")
            images_file = layout.require_product_member(
                images_file, "SLC", "images_file"
            )
        cases.append(
            GammaRadarSlcCase(
                name=name,
                sensor=sensor,
                raw_dir=raw_dir,
                slc_dir=slc_dir,
                images_file=images_file,
                polarizations=_polarizations(values.get("polarizations", "HH"), source),
                range_looks=_positive_integer(
                    values.get("range_looks", "10"), "range_looks", source
                ),
                azimuth_looks=_positive_integer(
                    values.get("azimuth_looks", "10"), "azimuth_looks", source
                ),
                commands=MappingProxyType(command_values),
                workflow_task=values.get("workflow_task", default_workflow_task),
                adapter_options=MappingProxyType(adapter_options),
                case_root=None if layout is None else layout.case_root,
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def load_gamma_radar_slc_config(path: str | Path) -> tuple[GammaRadarSlcCase, ...]:
    """Load GAMMA radar-grid cases using the permanent Workflow task identity."""

    return _load_sar_slc_config(
        path,
        default_workflow_task="PRE_GAMMA_SAR_SLC",
        sensors=_GAMMA_SENSORS,
    )


def load_other_sar_slc_config(path: str | Path) -> tuple[OtherSarSlcCase, ...]:
    """Load compatibility cases using the historical Workflow task identity."""

    return _load_sar_slc_config(
        path,
        default_workflow_task="PRE_OTHER_SAR_SLC",
        sensors=_LEGACY_SENSORS,
    )


def select_other_sar_slc_case(
    cases: Sequence[OtherSarSlcCase], name: str | None
) -> OtherSarSlcCase:
    """Select ``name`` or the only configured case, rejecting ambiguity."""

    if name is None:
        if len(cases) != 1:
            available = ", ".join(case.name for case in cases)
            raise ValueError(
                f"--case is required when configuration has multiple cases: {available}"
            )
        return cases[0]
    for case in cases:
        if case.name == name:
            return case
    available = ", ".join(case.name for case in cases)
    raise ValueError(f"unknown Other-SAR case {name!r}; available cases: {available}")


select_gamma_radar_slc_case = select_other_sar_slc_case


__all__ = [
    "GammaRadarSlcCase",
    "OtherSarSlcCase",
    "load_gamma_radar_slc_config",
    "load_other_sar_slc_config",
    "select_gamma_radar_slc_case",
    "select_other_sar_slc_case",
    "validate_safe_date",
    "validate_within_root",
]
