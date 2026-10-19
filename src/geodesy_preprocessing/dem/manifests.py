"""Atomic manifests for dataset-first topography products.

功能概述:
    为一套地形数据原子写入转换清单、数据集清单和项目地形目录。

函数说明:
    ``write_dataset_manifests`` 从已发布的 SOURCE/GAMMA 文件生成带摘要的清单。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .acquisition.opentopography import sha256_file
from .layout import TopographyDatasetLayout


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_dataset_manifests(
    layout: TopographyDatasetLayout,
    *,
    dem_type: str,
    gamma_metadata: Mapping[str, object] | None,
) -> None:
    """Write conversion, dataset, and project catalog manifests."""

    gamma: dict[str, object] | None = None
    if gamma_metadata is not None:
        conversion: dict[str, object] = {
            "schema_version": 1,
            "dataset_id": layout.dataset_id,
            "tool": "srtm2dem",
            "source": f"../SOURCE/{layout.source_raster.name}",
            "dem": layout.gamma_dem.name,
            "dem_par": layout.gamma_par.name,
            "log": layout.conversion_log.name,
            "dem_bytes": layout.gamma_dem.stat().st_size,
            "dem_sha256": sha256_file(layout.gamma_dem),
            "dem_par_sha256": sha256_file(layout.gamma_par),
        }
        for key in ("width", "nlines", "data_format", "dem_projection", "status"):
            if key in gamma_metadata:
                conversion[key] = gamma_metadata[key]
        _atomic_json(layout.conversion_manifest, conversion)
        gamma = {
            "dem": f"GAMMA/{layout.gamma_dem.name}",
            "dem_par": f"GAMMA/{layout.gamma_par.name}",
            "manifest": "GAMMA/conversion_manifest.json",
        }

    dataset_manifest: dict[str, object] = {
        "schema_version": 1,
        "dataset_id": layout.dataset_id,
        "dem_type": dem_type,
        "source": {
            "raster": f"SOURCE/{layout.source_raster.name}",
            "manifest": "SOURCE/download_manifest.json",
            "sha256": sha256_file(layout.source_raster),
        },
        "gamma": gamma,
        "usage": [],
    }
    if layout.topography_manifest.is_file():
        existing_dataset = json.loads(
            layout.topography_manifest.read_text(encoding="utf-8")
        )
        if isinstance(existing_dataset.get("usage"), list):
            dataset_manifest["usage"] = existing_dataset["usage"]
    _atomic_json(layout.topography_manifest, dataset_manifest)

    existing_entries: list[dict[str, object]] = []
    if layout.catalog.is_file():
        existing_catalog = json.loads(layout.catalog.read_text(encoding="utf-8"))
        raw_entries = existing_catalog.get("datasets", [])
        if isinstance(raw_entries, list):
            existing_entries = [entry for entry in raw_entries if isinstance(entry, dict)]
    prior = next(
        (entry for entry in existing_entries if entry.get("dataset_id") == layout.dataset_id),
        {},
    )
    entry: dict[str, object] = {
        "dataset_id": layout.dataset_id,
        "manifest": f"{layout.dataset_id}/topography_manifest.json",
        "dem_type": dem_type,
        "usage": prior.get("usage", dataset_manifest["usage"]),
    }
    datasets = [
        item for item in existing_entries if item.get("dataset_id") != layout.dataset_id
    ]
    datasets.append(entry)
    datasets.sort(key=lambda item: str(item.get("dataset_id", "")))
    _atomic_json(layout.catalog, {"schema_version": 1, "datasets": datasets})


__all__ = ["write_dataset_manifests"]
