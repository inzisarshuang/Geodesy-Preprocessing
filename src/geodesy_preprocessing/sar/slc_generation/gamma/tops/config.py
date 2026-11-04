"""Project-local configuration for GAMMA Sentinel-1 TOPS SLC generation.

功能概述:
    读取项目目录中的分节 ``.cfg``，合并公共默认值，解析相对项目根目录的路径，
    并将一个具名 case 转换为历史 S1-03 参数序列。

函数说明:
    ``GammaTopsSlcCase`` 保存一个不可变项目 case；``load_slc_config`` 读取并校验
    配置；``select_slc_case`` 选择唯一 case；``slc_case_arguments`` 生成兼容参数。
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.case_layout import optional_case_layout


_CASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_PROJECT_KEYS = {"root"}
_DEFAULT_KEYS = {
    "opod_dir",
    "range_looks",
    "azimuth_looks",
    "polarization",
    "workflow",
    "burst_coverage_plot",
}
_CASE_KEYS = _DEFAULT_KEYS | {
    "project",
    "processing_root",
    "raw_dir",
    "slc_dir",
    "figure_dir",
    "images",
    "burst_table",
    "roi_file",
    "reference_selector",
}
_REQUIRED_CASE_KEYS = {
    "raw_dir",
    "images",
    "burst_table",
    "opod_dir",
    "range_looks",
    "azimuth_looks",
    "polarization",
    "workflow",
}


@dataclass(frozen=True)
class GammaTopsSlcCase:
    """One resolved project-specific SLC generation case."""

    name: str
    raw_dir: Path
    slc_dir: Path
    opod_dir: Path | None
    images_file: Path
    burst_table: Path
    range_looks: int
    azimuth_looks: int
    polarization: str
    workflow_flag: int
    roi_file: Path | None
    reference_selector: str | None
    burst_coverage_plot: bool
    case_root: Path | None = None
    processing_root: Path | None = None
    figure_dir: Path | None = None


SlcConfigCase = GammaTopsSlcCase


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


def _boolean(value: str, name: str, source: Path) -> bool:
    normalized = value.strip().lower()
    choices = {
        "true": True,
        "yes": True,
        "1": True,
        "false": False,
        "no": False,
        "0": False,
    }
    try:
        return choices[normalized]
    except KeyError as error:
        raise ValueError(f"{source}: {name} must be true or false") from error


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def load_slc_config(
    path: str | Path, case_name: str | None = None
) -> tuple[SlcConfigCase, ...]:
    """Load ordered named cases from one strict project-local INI-style file."""

    source = Path(path).expanduser().resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with source.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except configparser.Error as error:
        raise ValueError(f"invalid SLC configuration {source}: {error}") from error

    if not parser.has_section("project"):
        raise ValueError(f"{source}: missing [project] section")
    project_section = parser["project"]
    _reject_unknown(project_section, _PROJECT_KEYS, source)
    if "root" not in project_section or not project_section["root"].strip():
        raise ValueError(f"{source}: [project] root is required")
    project_root = _resolve(source.parent, project_section["root"].strip())

    defaults: dict[str, str] = {}
    if parser.has_section("defaults"):
        defaults_section = parser["defaults"]
        _reject_unknown(defaults_section, _DEFAULT_KEYS, source)
        defaults.update(
            (key, value.strip()) for key, value in defaults_section.items()
        )

    invalid_sections = [
        name
        for name in parser.sections()
        if name not in {"project", "defaults"} and not name.startswith("case:")
    ]
    if invalid_sections:
        raise ValueError(
            f"{source}: unsupported section [{invalid_sections[0]}]; expected [case:name]"
        )

    case_sections = [
        name for name in parser.sections() if name.startswith("case:")
    ]
    if case_name is not None:
        requested = f"case:{case_name}"
        if requested not in case_sections:
            available = ", ".join(
                name.removeprefix("case:").strip() for name in case_sections
            )
            raise ValueError(
                f"unknown SLC case {case_name!r}; available cases: {available}"
            )
        case_sections = [requested]

    cases: list[SlcConfigCase] = []
    for section_name in case_sections:
        name = section_name.removeprefix("case:").strip()
        if not _CASE_NAME.fullmatch(name):
            raise ValueError(f"{source}: unsafe or empty SLC case name: {name!r}")
        section = parser[section_name]
        _reject_unknown(section, _CASE_KEYS, source)
        values = dict(defaults)
        values.update((key, value.strip()) for key, value in section.items())
        layout = optional_case_layout(project_root, values)
        required = set(_REQUIRED_CASE_KEYS)
        if layout is None:
            required.add("slc_dir")
        missing = sorted(key for key in required if not values.get(key))
        if missing:
            raise ValueError(
                f"{source}: [{section_name}] missing keys: {', '.join(missing)}"
            )
        workflow_text = values["workflow"].lower()
        workflow_values = {"zip": 0, "0": 0, "safe": 1, "1": 1}
        if workflow_text not in workflow_values:
            raise ValueError(
                f"{source}: [{section_name}] workflow must be safe/zip or 1/0"
            )
        opod_text = values["opod_dir"]
        roi_text = values.get("roi_file", "").strip()
        reference_selector = values.get("reference_selector", "").strip()
        raw_dir = _resolve(project_root, values["raw_dir"])
        slc_text = values.get("slc_dir", "").strip()
        slc_dir = (
            _resolve(project_root, slc_text)
            if slc_text
            else layout.product("SLC")  # type: ignore[union-attr]
        )
        opod_dir = None if opod_text == "-" else _resolve(project_root, opod_text)
        images_file = _resolve(project_root, values["images"])
        burst_table = _resolve(project_root, values["burst_table"])
        roi_file = _resolve(project_root, roi_text) if roi_text else None
        figure_text = values.get("figure_dir", "").strip()
        figure_dir = (
            _resolve(project_root, figure_text)
            if figure_text
            else (layout.figure("SLC") if layout is not None else slc_dir)
        )
        if layout is not None:
            raw_dir = layout.require_project_input(raw_dir, "raw_dir")
            slc_dir = layout.require_processing_path(slc_dir, "slc_dir")
            figure_dir = layout.require_processing_path(figure_dir, "figure_dir")
            if opod_dir is not None:
                opod_dir = layout.require_project_input(opod_dir, "opod_dir")
            images_file = layout.require_project_input(images_file, "images")
            burst_table = layout.require_project_input(burst_table, "burst_table")
            if roi_file is not None:
                roi_file = layout.require_project_input(roi_file, "roi_file")
        cases.append(
            SlcConfigCase(
                name=name,
                raw_dir=raw_dir,
                slc_dir=slc_dir,
                opod_dir=opod_dir,
                images_file=images_file,
                burst_table=burst_table,
                range_looks=_positive_integer(
                    values["range_looks"], "range_looks", source
                ),
                azimuth_looks=_positive_integer(
                    values["azimuth_looks"], "azimuth_looks", source
                ),
                polarization=values["polarization"],
                workflow_flag=workflow_values[workflow_text],
                roi_file=roi_file,
                reference_selector=reference_selector or None,
                burst_coverage_plot=_boolean(
                    values.get("burst_coverage_plot", "true"),
                    "burst_coverage_plot",
                    source,
                ),
                case_root=None if layout is None else layout.case_root,
                processing_root=None if layout is None else layout.processing_root,
                figure_dir=figure_dir,
            )
        )
    if not cases:
        raise ValueError(f"{source}: at least one [case:name] section is required")
    return tuple(cases)


def select_slc_case(
    cases: Sequence[SlcConfigCase], name: str | None
) -> SlcConfigCase:
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
    raise ValueError(f"unknown SLC case {name!r}; available cases: {available}")


def slc_case_arguments(case: SlcConfigCase) -> tuple[str, ...]:
    """Return the nine historical S1-03 arguments for one resolved case."""

    arguments = (
        str(case.raw_dir),
        str(case.slc_dir),
        str(case.opod_dir) if case.opod_dir is not None else "-",
        str(case.images_file),
        str(case.burst_table),
        str(case.range_looks),
        str(case.azimuth_looks),
        case.polarization,
        str(case.workflow_flag),
    )
    if case.processing_root is not None and case.figure_dir is not None:
        return arguments + ("--figure-dir", str(case.figure_dir))
    return arguments


__all__ = [
    "GammaTopsSlcCase",
    "SlcConfigCase",
    "load_slc_config",
    "select_slc_case",
    "slc_case_arguments",
]
