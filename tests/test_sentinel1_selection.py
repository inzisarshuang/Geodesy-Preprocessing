"""Tests for deterministic Sentinel-1 direction, track, and burst selection."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from shapely import wkt
from shapely.geometry import box

from geodesy_preprocessing.sar.acquisition.sentinel1.models import (
    Sentinel1AcquisitionCase,
    Sentinel1Product,
    Sentinel1ProductType,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.selection import (
    select_sentinel1_products,
)


def _product(
    identity: str,
    *,
    date: str = "2026-09-08",
    track: int = 154,
    direction: str = "DESCENDING",
    footprint=(-5.0, 38.0, -3.0, 42.0),
    product_type=Sentinel1ProductType.SLC,
    swath=None,
    burst_id=None,
    merge_group=None,
) -> Sentinel1Product:
    return Sentinel1Product(
        product_id=identity, product_type=product_type, acquisition_date=date,
        start_time=f"{date}T06:00:00Z", end_time=f"{date}T06:00:25Z",
        relative_orbit=track, absolute_orbit=65000, orbit_direction=direction,
        beam_mode="IW", polarizations=("VH", "VV"),
        footprint_wkt=wkt.dumps(box(*footprint)), file_name=f"{identity}.zip",
        size_bytes=100, url=f"https://example.test/{identity}.zip",
        swath=swath, burst_index=burst_id, relative_burst_id=burst_id,
        full_burst_id=(f"{track}_{swath}_{burst_id}" if burst_id else None),
        merge_group=merge_group,
    )


class Sentinel1SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path("/tmp/project")
        self.case = Sentinel1AcquisitionCase(
            name="main", project_root=root, kml_file=None, bbox=(-5, 38, -1, 42),
            start_date="2026-09-08", end_date="2026-09-10",
            product_type=Sentinel1ProductType.SLC, beam_mode="IW",
            polarizations=("VV",), provider="asf", tracks=None,
            orbit_direction=None, coverage_threshold=0.99,
            query_dir=root / "Q", download_dir=root / "D", download_threads=2,
            max_retries=3, retry_delay_seconds=1, keep_burst_files=True,
        )
        self.aoi = box(-5, 38, -1, 42)

    def test_auto_direction_and_smallest_track_combination_cover_every_date(self) -> None:
        products = (
            _product("d154a", footprint=(-5, 38, -3, 42)),
            _product("d155a", track=155, footprint=(-3, 38, -1, 42)),
            _product("d154b", date="2026-09-09", footprint=(-5, 38, -3, 42)),
            _product("d155b", date="2026-09-09", track=155, footprint=(-3, 38, -1, 42)),
            _product("a010a", track=10, direction="ASCENDING", footprint=(-5, 38, -1, 42)),
            _product("a010b", date="2026-09-09", track=10, direction="ASCENDING", footprint=(-5, 38, -1, 42)),
        )
        result = select_sentinel1_products(self.case, products, self.aoi)
        self.assertEqual(result.orbit_direction, "ASCENDING")
        self.assertEqual(result.tracks, (10,))
        self.assertEqual(tuple(item.product_id for item in result.products), ("a010a", "a010b"))
        self.assertEqual(tuple(date for date, _ in result.date_coverage), ("2026-09-08", "2026-09-09"))

    def test_manual_direction_tracks_and_polarization_are_strict(self) -> None:
        case = replace(self.case, orbit_direction="DESCENDING", tracks=(154,))
        wrong_pol = replace(_product("wrong"), polarizations=("HH",))
        result = select_sentinel1_products(
            case,
            (
                _product("keep", footprint=(-5, 38, -1, 42)),
                _product("other", track=155, footprint=(-5, 38, -1, 42)),
                wrong_pol,
            ),
            self.aoi,
        )
        self.assertEqual(tuple(item.product_id for item in result.products), ("keep",))

    def test_rejects_when_no_track_choice_meets_per_date_threshold(self) -> None:
        with self.assertRaisesRegex(ValueError, "coverage threshold"):
            select_sentinel1_products(
                self.case,
                (_product("half", footprint=(-5, 38, -3, 42)),),
                self.aoi,
            )

    def test_burst_selection_rejects_incomplete_identity_and_orders_supplements(self) -> None:
        case = replace(self.case, product_type=Sentinel1ProductType.BURST, tracks=(154,), orbit_direction="DESCENDING", coverage_threshold=0.45)
        incomplete = _product("bad", product_type=Sentinel1ProductType.BURST)
        with self.assertRaisesRegex(ValueError, "bad.*burst identity"):
            select_sentinel1_products(case, (incomplete,), self.aoi)

        roi = _product("roi", product_type=Sentinel1ProductType.BURST, footprint=(-5, 38, -3, 42), swath="IW2", burst_id=7, merge_group="G")
        supplement_9 = _product("supp9", product_type=Sentinel1ProductType.BURST, footprint=(5, 50, 6, 51), swath="IW3", burst_id=9, merge_group="G")
        supplement_8 = _product("supp8", product_type=Sentinel1ProductType.BURST, footprint=(5, 50, 6, 51), swath="IW1", burst_id=8, merge_group="G")
        result = select_sentinel1_products(case, (supplement_9, roi, supplement_8), self.aoi)
        self.assertEqual(tuple(item.product_id for item in result.products), ("supp8", "roi", "supp9"))
        self.assertEqual(
            tuple((item.roi_match, item.supplemental) for item in result.products),
            ((False, True), (True, False), (False, True)),
        )


if __name__ == "__main__":
    unittest.main()
