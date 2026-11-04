"""Canonical project/case paths owned by Geodesy-Preprocessing.

功能概述:
    绑定项目内的 ``<sensor>/<case>/<method>`` 处理根目录，并给出预处理产品、
    集中快视图和日志的默认位置；解析符号链接后拒绝跨项目和跨 case 路径。

函数说明:
    ``PreprocessingCaseLayout`` 保存不可变路径边界并校验标准产品路径；
    ``optional_case_layout`` 为新 CFG 启用边界，同时保留未迁移旧 CFG 的兼容性；
    ``resolve_case_log_path`` 安全解析 case-local 预处理日志目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import validate_within_root


_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_PRODUCTS = {
    "SLC": ("SLC",),
    "GEOMETRY": ("RSLC", "GEOCODING"),
    "RSLC": ("RSLC",),
    "RSLC_SUBSET": ("RSLC", "SUBSET"),
}
_CONFIG_STAGES = {
    "acquisition": "0acquisition",
    "preprocessing": "1preprocessing",
    "analysis": "2analysis",
    "adapter": "3adapter",
    "visualization": "4visualization",
}


@dataclass(frozen=True)
class PreprocessingCaseLayout:
    """Resolved canonical paths for one ``<sensor>/<case>`` directory."""

    project_root: Path
    case_root: Path
    processing_root: Path
    case_id: str

    @classmethod
    def bind(
        cls,
        project_root: str | Path,
        case_root: str | Path,
        processing_root: str | Path,
    ) -> "PreprocessingCaseLayout":
        """Bind one case and its single-component processing method safely."""

        project = Path(project_root).expanduser().resolve()
        raw = Path(case_root).expanduser()
        if raw.is_absolute() or len(raw.parts) != 2:
            raise ValueError(
                "case_root must be a project-relative <sensor>/<case> path"
            )
        if any(not _COMPONENT.fullmatch(part) for part in raw.parts):
            raise ValueError(
                "case_root must be a project-relative <sensor>/<case> path"
            )
        resolved = validate_within_root(project / raw, project, "project")
        raw_processing = Path(processing_root).expanduser()
        if (
            raw_processing.is_absolute()
            or len(raw_processing.parts) != 3
            or raw_processing.parts[:2] != raw.parts
            or any(not _COMPONENT.fullmatch(part) for part in raw_processing.parts)
        ):
            raise ValueError(
                "processing_root must be a project-relative "
                "<sensor>/<case>/<method> path inside the declared case"
            )
        processing = validate_within_root(
            project / raw_processing, resolved, "processing_root"
        )
        return cls(
            project_root=project,
            case_root=resolved,
            processing_root=processing,
            case_id=raw.as_posix(),
        )

    @property
    def log_root(self) -> Path:
        """Return the method-local log root."""

        return self.processing_root / "LOG"

    @property
    def config_root(self) -> Path:
        """Return the research-project configuration root."""

        return self.project_root / "config"

    def config(self, stage: str) -> Path:
        """Return one standard case-local configuration stage directory."""

        normalized = stage.strip().lower()
        if normalized not in _CONFIG_STAGES:
            allowed = ", ".join(sorted(_CONFIG_STAGES))
            raise ValueError(f"unknown case config stage {stage!r}; expected {allowed}")
        return self.config_root / _CONFIG_STAGES[normalized]

    @property
    def pre_root(self) -> Path:
        """Return the processing-method root for compatibility callers."""

        return self.processing_root

    @property
    def acquisition_manifest(self) -> Path:
        """Return the case acquisition manifest path."""

        return self.case_root / "acquisition_manifest.json"

    @property
    def analysis_root(self) -> Path:
        """Return the shared project contract path for downstream analysis."""

        return self.processing_root

    @property
    def visualization_root(self) -> Path:
        """Return the shared project contract path for downstream visualization."""

        return self.processing_root / "FIGURES"

    def product(self, name: str) -> Path:
        """Return one canonical case-local GAMMA preprocessing product path."""

        normalized = name.strip().upper()
        if normalized not in _PRODUCTS:
            allowed = ", ".join(sorted(_PRODUCTS))
            raise ValueError(f"unknown preprocessing product {name!r}; expected {allowed}")
        return self.processing_root.joinpath(*_PRODUCTS[normalized])

    @staticmethod
    def _safe_parts(parts: tuple[str, ...], label: str) -> tuple[str, ...]:
        if not parts:
            raise ValueError(f"{label} path requires at least one component")
        for part in parts:
            component = Path(part)
            if (
                component.is_absolute()
                or len(component.parts) != 1
                or part in {"", ".", ".."}
            ):
                raise ValueError(f"unsafe {label} path component: {part!r}")
        return parts

    def figure(self, *parts: str) -> Path:
        """Return a centralized preprocessing quicklook directory."""

        return (self.processing_root / "FIGURES" / "PRE").joinpath(
            *self._safe_parts(parts, "figure")
        )

    def log(self, *parts: str) -> Path:
        """Return a method-local preprocessing log directory."""

        return self.log_root.joinpath(*self._safe_parts(parts, "log"))

    def require_product(self, path: str | Path, product: str, label: str) -> Path:
        """Require ``path`` to equal the canonical path for ``product``."""

        resolved = validate_within_root(Path(path), self.project_root, label)
        expected = self.product(product)
        if resolved != expected:
            raise ValueError(
                f"{label} must use canonical case path {expected}; got {resolved}"
            )
        return resolved

    def require_product_member(
        self, path: str | Path, product: str, label: str
    ) -> Path:
        """Require ``path`` to remain below one canonical product directory."""

        resolved = self.require_project_input(path, label)
        expected_root = self.product(product)
        if not resolved.is_relative_to(expected_root):
            raise ValueError(
                f"{label} must use canonical case path below {expected_root}; "
                f"got {resolved}"
            )
        return resolved

    def require_project_input(self, path: str | Path, label: str) -> Path:
        """Require an input path to remain inside the research project."""

        return validate_within_root(Path(path), self.project_root, label)

    def require_processing_path(self, path: str | Path, label: str) -> Path:
        """Require a configurable result path to remain below the method root."""

        return validate_within_root(Path(path), self.processing_root, label)


def optional_case_layout(
    project_root: Path, values: dict[str, str]
) -> PreprocessingCaseLayout | None:
    """Return an opt-in layout for new CFGs while retaining legacy CFG support."""

    project = values.get("project", "").strip()
    processing_root = values.get("processing_root", "").strip()
    if bool(project) != bool(processing_root):
        raise ValueError("project and processing_root must be provided together")
    return (
        None
        if not project
        else PreprocessingCaseLayout.bind(project_root, project, processing_root)
    )


def resolve_case_log_path(case_root: str | Path, *parts: str) -> Path:
    """Resolve a safe path below ``<case>/LOG/PRE/GAMMA``."""

    root = Path(case_root).expanduser().resolve()
    for part in parts:
        component = Path(part)
        if component.is_absolute() or len(component.parts) != 1 or part in {"", ".", ".."}:
            raise ValueError(f"unsafe case log path component: {part!r}")
    resolved = (root / "LOG/PRE/GAMMA").joinpath(*parts).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"case log path is outside case root: {resolved}")
    return resolved


__all__ = [
    "PreprocessingCaseLayout",
    "optional_case_layout",
    "resolve_case_log_path",
]
