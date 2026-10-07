"""Immutable models for GAMMA radar-grid SLC generation.

功能概述:
    描述供应商产品、外部命令、待发布文件和单景执行计划，避免运行阶段修改计划。

函数说明:
    ``SourceProduct`` 描述只读输入；``CommandSpec`` 描述无 shell 的命令；
    ``ProductOutput`` 映射暂存文件与最终文件；``ScenePlan`` 描述一景任务。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class SourceProduct:
    """One structurally validated vendor acquisition."""

    sensor: str
    date: str
    root: Path
    source_files: tuple[Path, ...]
    polarizations: tuple[str, ...]


@dataclass(frozen=True)
class CommandSpec:
    """One ordered external command and its human-readable stage name."""

    stage: str
    argv: tuple[str, ...]
    parameter_file: Path | None = None


@dataclass(frozen=True)
class ProductOutput:
    """One required staged file and its final destination."""

    polarization: str
    staged_relative: Path
    destination: Path


@dataclass(frozen=True)
class ScenePlan:
    """Immutable execution plan for one acquisition."""

    product: SourceProduct
    commands: tuple[CommandSpec, ...]
    outputs: tuple[ProductOutput, ...]
    state: Literal["run", "skip"]


__all__ = ["CommandSpec", "ProductOutput", "ScenePlan", "SourceProduct"]
