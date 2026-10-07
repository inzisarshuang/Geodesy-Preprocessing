"""Sentinel-1 查询、选择与审查产物的事务发布。

功能概述:
    执行 ASF 元数据查询和本地覆盖选择，把 CSV、覆盖图、URL、日期列表及带摘要的
    查询清单作为一个完整目录发布；本模块不下载影像。

函数说明:
    ``Sentinel1QueryResult`` 保存发布结果；``publish_sentinel1_query`` 执行查询发布；
    ``load_query_for_download`` 校验并重读不可变的选中产品表。
"""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from shapely import wkt
from shapely.geometry import box
from shapely.ops import unary_union

from geodesy_preprocessing.sar.common.coverage import coverage_fraction
from geodesy_preprocessing.sar.common.roi import load_kml_roi

from .manifest import (
    Sentinel1QueryManifest,
    aoi_sha256,
    query_fingerprint,
    read_query_manifest,
    sha256_file,
    validate_query_for_download,
    write_query_manifest,
)
from .models import Sentinel1AcquisitionCase, Sentinel1Product, Sentinel1ProductType
from .plotting import save_coverage_figure
from .search import Sentinel1SearchClient, search_sentinel1
from .selection import Sentinel1Selection, select_sentinel1_products


_PRODUCT_COLUMNS = (
    "product_id", "product_type", "acquisition_date", "start_time", "end_time",
    "relative_orbit", "absolute_orbit", "orbit_direction", "beam_mode",
    "polarizations", "footprint_wkt", "file_name", "size_bytes", "url", "swath",
    "burst_index", "relative_burst_id", "full_burst_id", "merge_group",
    "roi_match", "supplemental",
)


@dataclass(frozen=True)
class Sentinel1QueryResult:
    """Published all/selected products and matching query identity."""

    query_dir: Path
    all_products: tuple[Sentinel1Product, ...]
    selected_products: tuple[Sentinel1Product, ...]
    selection: Sentinel1Selection | None
    manifest: Sentinel1QueryManifest


def _aoi(case: Sentinel1AcquisitionCase):
    return load_kml_roi(case.kml_file) if case.kml_file is not None else box(*case.bbox)


def _write_products(path: Path, products: Sequence[Sentinel1Product]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=_PRODUCT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for item in products:
            writer.writerow({
                "product_id": item.product_id,
                "product_type": item.product_type.value,
                "acquisition_date": item.acquisition_date,
                "start_time": item.start_time,
                "end_time": item.end_time,
                "relative_orbit": item.relative_orbit,
                "absolute_orbit": item.absolute_orbit,
                "orbit_direction": item.orbit_direction,
                "beam_mode": item.beam_mode,
                "polarizations": ",".join(item.polarizations),
                "footprint_wkt": item.footprint_wkt,
                "file_name": item.file_name,
                "size_bytes": item.size_bytes,
                "url": item.url,
                "swath": item.swath or "",
                "burst_index": "" if item.burst_index is None else item.burst_index,
                "relative_burst_id": "" if item.relative_burst_id is None else item.relative_burst_id,
                "full_burst_id": item.full_burst_id or "",
                "merge_group": item.merge_group or "",
                "roi_match": "true" if item.roi_match else "false",
                "supplemental": "true" if item.supplemental else "false",
            })


def _read_products(path: Path) -> tuple[Sentinel1Product, ...]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != _PRODUCT_COLUMNS:
                raise ValueError("unexpected columns")
            return tuple(Sentinel1Product(
                product_id=row["product_id"],
                product_type=Sentinel1ProductType(row["product_type"]),
                acquisition_date=row["acquisition_date"],
                start_time=row["start_time"], end_time=row["end_time"],
                relative_orbit=int(row["relative_orbit"]), absolute_orbit=int(row["absolute_orbit"]),
                orbit_direction=row["orbit_direction"], beam_mode=row["beam_mode"],
                polarizations=tuple(value for value in row["polarizations"].split(",") if value),
                footprint_wkt=row["footprint_wkt"], file_name=row["file_name"],
                size_bytes=int(row["size_bytes"]), url=row["url"],
                swath=row["swath"] or None,
                burst_index=int(row["burst_index"]) if row["burst_index"] else None,
                relative_burst_id=int(row["relative_burst_id"]) if row["relative_burst_id"] else None,
                full_burst_id=row["full_burst_id"] or None, merge_group=row["merge_group"] or None,
                roi_match=row["roi_match"] == "true", supplemental=row["supplemental"] == "true",
            ) for row in reader)
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid Sentinel-1 selected CSV: {path}") from error


def _write_track_table(path: Path, products: Sequence[Sentinel1Product], aoi) -> None:
    groups: dict[int, list[Sentinel1Product]] = defaultdict(list)
    for item in products:
        if item.roi_match:
            groups[item.relative_orbit].append(item)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("track", "dates", "product_count", "minimum_date_coverage", "product_ids"))
        for track, entries in sorted(groups.items()):
            by_date: dict[str, list] = defaultdict(list)
            for item in entries:
                by_date[item.acquisition_date].append(wkt.loads(item.footprint_wkt))
            coverages = [coverage_fraction(aoi, unary_union(parts)) for parts in by_date.values()]
            ordered = sorted(entries, key=lambda item: (item.start_time, item.product_id))
            writer.writerow((
                track, ",".join(sorted(by_date)), len(entries),
                f"{min(coverages):.12f}", ",".join(item.product_id for item in ordered),
            ))


def _write_lists(staging: Path, products: Sequence[Sentinel1Product], product_type: Sentinel1ProductType) -> None:
    groups: dict[int, list[Sentinel1Product]] = defaultdict(list)
    for item in products:
        groups[item.relative_orbit].append(item)
    for track, entries in sorted(groups.items()):
        ordered = sorted(entries, key=lambda item: (item.start_time, item.product_id))
        (staging / f"S1_{product_type.value}_download_urls_track{track:03d}.txt").write_text(
            "".join(f"{item.url}\n" for item in ordered), encoding="utf-8"
        )
        dates = sorted({item.acquisition_date.replace("-", "") for item in ordered})
        (staging / f"images_{track:03d}").write_text(
            "".join(f"{date}\n" for date in dates), encoding="utf-8"
        )


def _publish_directory(staging: Path, destination: Path) -> None:
    backup = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".backup", dir=destination.parent))
    backup.rmdir()
    moved = False
    try:
        if destination.exists():
            os.replace(destination, backup)
            moved = True
        os.replace(staging, destination)
    except BaseException:
        if moved and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        if moved:
            shutil.rmtree(backup)


def publish_sentinel1_query(
    case: Sentinel1AcquisitionCase, client: Sentinel1SearchClient
) -> Sentinel1QueryResult:
    """Publish deterministic Sentinel-1 query products without downloading data."""

    all_products = search_sentinel1(case, client)
    aoi = _aoi(case)
    selection = select_sentinel1_products(case, all_products, aoi)
    selected = selection.products
    destination = case.query_dir
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".stage", dir=destination.parent))
    try:
        prefix = f"S1_{case.product_type.value}"
        _write_products(staging / f"{prefix}_all.csv", all_products)
        _write_products(staging / f"{prefix}_selected.csv", selected)
        _write_track_table(staging / f"{prefix}_track_coverage.csv", all_products, aoi)
        _write_lists(staging, selected, case.product_type)
        save_coverage_figure(all_products, aoi, staging / f"{prefix}_coverage_all.png", f"Sentinel-1 {case.product_type.value}: all")
        save_coverage_figure(selected, aoi, staging / f"{prefix}_coverage_selected.png", f"Sentinel-1 {case.product_type.value}: selected")
        digest = aoi_sha256(case)
        artifacts = tuple(sorted((path.name, sha256_file(path)) for path in staging.iterdir() if path.is_file()))
        manifest = Sentinel1QueryManifest(
            schema_version=1, case_name=case.name,
            fingerprint=query_fingerprint(case, digest), aoi_sha256=digest,
            selected_product_ids=tuple(item.product_id for item in selected),
            artifact_digests=artifacts,
        )
        write_query_manifest(manifest, staging / "query_manifest.json")
        _publish_directory(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return Sentinel1QueryResult(destination, all_products, selected, selection, manifest)


def load_query_for_download(case: Sentinel1AcquisitionCase) -> Sentinel1QueryResult:
    """Validate and reload one published selected product table."""

    manifest = read_query_manifest(case.query_dir / "query_manifest.json")
    validate_query_for_download(case, manifest)
    products = _read_products(case.query_dir / f"S1_{case.product_type.value}_selected.csv")
    if tuple(item.product_id for item in products) != manifest.selected_product_ids:
        raise ValueError("Sentinel-1 selected CSV products do not match query manifest")
    return Sentinel1QueryResult(case.query_dir, products, products, None, manifest)


__all__ = ["Sentinel1QueryResult", "load_query_for_download", "publish_sentinel1_query"]
