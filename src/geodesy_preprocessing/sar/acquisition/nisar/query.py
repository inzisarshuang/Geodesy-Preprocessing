"""NISAR 覆盖查询产物的筛选与事务发布。

功能概述:
    查询并规范化产品，应用人工轨道/帧、频率和极化条件，生成 S1 风格覆盖图、
    确定性 CSV、逐轨 URL 文件与带摘要的查询清单；本模块不执行下载。

函数说明:
    ``NisarQueryResult`` 保存已发布查询结果；``run_nisar_query`` 执行只读查询并将
    全部产物作为一个目录事务发布；``load_query_for_download`` 重读筛选表和清单。
"""

from __future__ import annotations

import csv
import hashlib
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from shapely import wkt

from geodesy_preprocessing.sar.common.roi import load_kml_roi

from .coverage import coverage_fraction, summarize_tracks
from .manifest import (
    NisarQueryManifest,
    query_fingerprint,
    read_query_manifest,
    write_query_manifest,
)
from .models import NisarFrequencyMode, NisarGranule, NisarProductType, NisarSearchCase
from .plotting import save_coverage_figure
from .search import NisarSearchClient, search_nisar


_GRANULE_COLUMNS = (
    "granule_id",
    "product_type",
    "acquisition_date",
    "start_time",
    "end_time",
    "track",
    "frame",
    "orbit_direction",
    "coverage_fraction",
    "size_bytes",
    "url",
    "frequencies",
    "polarizations",
    "checksum",
)


@dataclass(frozen=True)
class NisarQueryResult:
    """Published all/selected products and the matching download identity."""

    query_dir: Path
    all_granules: tuple[NisarGranule, ...]
    selected_granules: tuple[NisarGranule, ...]
    manifest: NisarQueryManifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _selected(case: NisarSearchCase, granule: NisarGranule) -> bool:
    required_frequencies = (
        {"A", "B"}
        if case.frequency_mode is NisarFrequencyMode.MAIN_PLUS_AUXILIARY
        else {"A"}
    )
    return (
        granule.product_type is case.product_type
        and (case.tracks is None or granule.track in case.tracks)
        and (case.frames is None or granule.frame in case.frames)
        and (case.orbit_direction is None or granule.orbit_direction == case.orbit_direction)
        and required_frequencies.issubset(granule.frequencies)
        and set(case.polarizations).issubset(granule.polarizations)
    )


def _write_granules(path: Path, granules: Sequence[NisarGranule], aoi) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(_GRANULE_COLUMNS)
        for granule in granules:
            writer.writerow(
                (
                    granule.granule_id,
                    granule.product_type.value,
                    granule.acquisition_date,
                    granule.start_time,
                    granule.end_time,
                    granule.track,
                    granule.frame,
                    granule.orbit_direction,
                    f"{coverage_fraction(aoi, wkt.loads(granule.footprint_wkt)):.12f}",
                    granule.size_bytes,
                    granule.url,
                    ",".join(granule.frequencies),
                    ",".join(granule.polarizations),
                    granule.checksum or "",
                )
            )


def _write_tracks(path: Path, granules: Sequence[NisarGranule], aoi) -> None:
    summaries = summarize_tracks(granules, aoi)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("track", "frames", "granule_count", "coverage_fraction", "granule_ids"))
        for summary in summaries:
            writer.writerow(
                (
                    summary.track,
                    ",".join(str(item) for item in summary.frames),
                    summary.granule_count,
                    f"{summary.coverage_fraction:.12f}",
                    ",".join(summary.granule_ids),
                )
            )


def _write_urls(
    staging: Path,
    granules: Sequence[NisarGranule],
    product_type: NisarProductType,
) -> None:
    groups: dict[int, list[NisarGranule]] = defaultdict(list)
    for granule in granules:
        groups[granule.track].append(granule)
    for track, entries in sorted(groups.items()):
        ordered = sorted(entries, key=lambda item: (item.start_time, item.frame, item.granule_id))
        (staging / f"NISAR_{product_type.value}_download_urls_track{track:03d}.txt").write_text(
            "".join(f"{item.url}\n" for item in ordered), encoding="utf-8"
        )


def _publish_directory(staging: Path, destination: Path) -> None:
    backup = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".backup", dir=destination.parent)
    )
    backup.rmdir()
    moved_old = False
    try:
        if destination.exists():
            os.replace(destination, backup)
            moved_old = True
        os.replace(staging, destination)
    except BaseException:
        if moved_old and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        if moved_old:
            shutil.rmtree(backup)


def run_nisar_query(
    case: NisarSearchCase, client: NisarSearchClient
) -> NisarQueryResult:
    """Publish deterministic NISAR query products without downloading data."""

    all_granules = search_nisar(case, client)
    selected = tuple(item for item in all_granules if _selected(case, item))
    aoi = load_kml_roi(case.kml_file)
    destination = case.query_dir
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".stage", dir=destination.parent)
    )
    try:
        prefix = f"NISAR_{case.product_type.value}"
        _write_granules(staging / f"{prefix}_all.csv", all_granules, aoi)
        _write_granules(staging / f"{prefix}_filtered.csv", selected, aoi)
        _write_tracks(staging / f"{prefix}_track_coverage.csv", all_granules, aoi)
        _write_urls(staging, selected, case.product_type)
        save_coverage_figure(
            all_granules,
            aoi,
            staging / f"{prefix}_footprints_all.png",
            f"NISAR {case.product_type.value}: all query results",
        )
        save_coverage_figure(
            selected,
            aoi,
            staging / f"{prefix}_footprints_selected.png",
            f"NISAR {case.product_type.value}: selected results",
        )
        kml_digest = _sha256_file(case.kml_file)
        artifacts = tuple(
            sorted(
                (path.name, _sha256_file(path))
                for path in staging.iterdir()
                if path.is_file()
            )
        )
        manifest = NisarQueryManifest(
            schema_version=1,
            case_name=case.name,
            fingerprint=query_fingerprint(case, kml_digest),
            kml_sha256=kml_digest,
            selected_granule_ids=tuple(item.granule_id for item in selected),
            artifact_digests=artifacts,
        )
        write_query_manifest(manifest, staging / "query_manifest.json")
        _publish_directory(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return NisarQueryResult(
        query_dir=destination,
        all_granules=all_granules,
        selected_granules=selected,
        manifest=manifest,
    )


def load_query_for_download(case: NisarSearchCase) -> NisarQueryResult:
    """Load the published filtered CSV and manifest for a download run."""

    manifest = read_query_manifest(case.query_dir / "query_manifest.json")
    filtered = case.query_dir / f"NISAR_{case.product_type.value}_filtered.csv"
    try:
        with filtered.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != _GRANULE_COLUMNS:
                raise ValueError("NISAR filtered CSV has unexpected columns")
            granules = tuple(
                NisarGranule(
                    granule_id=row["granule_id"],
                    product_type=NisarProductType(row["product_type"]),
                    acquisition_date=row["acquisition_date"],
                    start_time=row["start_time"],
                    end_time=row["end_time"],
                    track=int(row["track"]),
                    frame=int(row["frame"]),
                    orbit_direction=row["orbit_direction"],
                    footprint_wkt="",
                    size_bytes=int(row["size_bytes"]),
                    url=row["url"],
                    frequencies=tuple(item for item in row["frequencies"].split(",") if item),
                    polarizations=tuple(item for item in row["polarizations"].split(",") if item),
                    checksum=row["checksum"] or None,
                )
                for row in reader
            )
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid NISAR filtered CSV: {filtered}") from error
    if tuple(item.granule_id for item in granules) != manifest.selected_granule_ids:
        raise ValueError("NISAR filtered CSV granules do not match query manifest")
    return NisarQueryResult(
        query_dir=case.query_dir,
        all_granules=granules,
        selected_granules=granules,
        manifest=manifest,
    )


__all__ = ["NisarQueryResult", "load_query_for_download", "run_nisar_query"]
