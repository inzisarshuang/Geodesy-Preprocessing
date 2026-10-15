"""Canonical project-level paths for reusable topography datasets.

功能概述:
    将项目根目录和安全的数据集 ID 绑定到唯一的
    ``TOPOGRAPHY/<dataset_id>/{SOURCE,GAMMA}`` 目录结构。

函数说明:
    ``TopographyDatasetLayout`` 提供来源栅格、GAMMA DEM 及各级清单的标准路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.paths import validate_within_root


_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


@dataclass(frozen=True)
class TopographyDatasetLayout:
    """Resolved ``TOPOGRAPHY/<dataset_id>/{SOURCE,GAMMA}`` path contract."""

    project_root: Path
    dataset_id: str
    topography_root: Path
    root: Path

    @classmethod
    def bind(
        cls, project_root: str | Path, dataset_id: str
    ) -> "TopographyDatasetLayout":
        """Bind one safe project-local topography dataset identifier."""

        project = Path(project_root).expanduser().resolve()
        raw_id = str(dataset_id).strip()
        component = Path(raw_id)
        if (
            not raw_id
            or component.is_absolute()
            or len(component.parts) != 1
            or raw_id in {".", ".."}
            or not _COMPONENT.fullmatch(raw_id)
        ):
            raise ValueError(
                "topography_dataset_id must be one safe directory component"
            )
        topography = validate_within_root(
            project / "TOPOGRAPHY", project, "topography root"
        )
        root = validate_within_root(
            topography / raw_id, project, "topography dataset"
        )
        return cls(
            project_root=project,
            dataset_id=raw_id,
            topography_root=topography,
            root=root,
        )

    @property
    def source_dir(self) -> Path:
        return self.root / "SOURCE"

    @property
    def source_raster(self) -> Path:
        return self.source_dir / f"{self.dataset_id}.tif"

    @property
    def download_manifest(self) -> Path:
        return self.source_dir / "download_manifest.json"

    @property
    def gamma_dir(self) -> Path:
        return self.root / "GAMMA"

    @property
    def gamma_dem(self) -> Path:
        return self.gamma_dir / f"{self.dataset_id}.dem"

    @property
    def gamma_par(self) -> Path:
        return self.gamma_dir / f"{self.dataset_id}.dem.par"

    @property
    def conversion_log(self) -> Path:
        return self.gamma_dir / "conversion.log"

    @property
    def conversion_manifest(self) -> Path:
        return self.gamma_dir / "conversion_manifest.json"

    @property
    def topography_manifest(self) -> Path:
        return self.root / "topography_manifest.json"

    @property
    def catalog(self) -> Path:
        return self.topography_root / "catalog.json"


__all__ = ["TopographyDatasetLayout"]
