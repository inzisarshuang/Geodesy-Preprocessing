"""Tests for NISAR equal-area footprint coverage and track ranking."""

from __future__ import annotations

import unittest

from shapely import wkt
from shapely.geometry import LineString, Polygon, box

from geodesy_preprocessing.sar.acquisition.nisar.coverage import (
    coverage_fraction,
    summarize_tracks,
)
from geodesy_preprocessing.sar.acquisition.nisar.models import (
    NisarGranule,
    NisarProductType,
)
from geodesy_preprocessing.sar.common.coverage import coverage_fraction as common_fraction


def _granule(
    granule_id: str,
    track: int,
    frame: int,
    footprint: Polygon,
) -> NisarGranule:
    return NisarGranule(
        granule_id=granule_id,
        product_type=NisarProductType.RSLC,
        acquisition_date="2026-01-05",
        start_time="2026-01-05T10:00:00Z",
        end_time="2026-01-05T10:01:00Z",
        track=track,
        frame=frame,
        orbit_direction="ASCENDING",
        footprint_wkt=wkt.dumps(footprint, rounding_precision=12, trim=True),
        size_bytes=1,
        url=f"https://example.test/{granule_id}.h5",
        frequencies=("A",),
        polarizations=("HH",),
    )


class NisarCoverageTests(unittest.TestCase):
    """Catch geographic-area and grouping errors in human track selection."""

    def test_equal_area_fraction_handles_none_full_and_half_overlap(self) -> None:
        aoi = box(-1.0, -1.0, 1.0, 1.0)

        self.assertEqual(coverage_fraction(aoi, box(10.0, 10.0, 11.0, 11.0)), 0.0)
        self.assertAlmostEqual(coverage_fraction(aoi, box(-2.0, -2.0, 2.0, 2.0)), 1.0)
        self.assertAlmostEqual(
            coverage_fraction(aoi, box(0.0, -1.0, 1.0, 1.0)),
            0.5,
            places=4,
        )

    def test_nisar_coverage_uses_the_shared_equal_area_primitive(self) -> None:
        aoi = box(-1.0, -1.0, 1.0, 1.0)
        footprint = box(0.0, -1.0, 1.0, 1.0)

        self.assertIs(coverage_fraction, common_fraction)
        self.assertEqual(coverage_fraction(aoi, footprint), common_fraction(aoi, footprint))

    def test_crossing_antimeridian_is_treated_as_two_degrees_not_358(self) -> None:
        aoi = Polygon(
            [(179.0, -1.0), (-179.0, -1.0), (-179.0, 1.0), (179.0, 1.0)]
        )
        inner_half = Polygon(
            [(179.5, -1.0), (-179.5, -1.0), (-179.5, 1.0), (179.5, 1.0)]
        )

        self.assertAlmostEqual(coverage_fraction(aoi, inner_half), 0.5, places=3)

    def test_rejects_empty_invalid_or_non_polygon_geometry(self) -> None:
        aoi = box(0.0, 0.0, 1.0, 1.0)
        bowtie = Polygon([(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)])
        invalid = (Polygon(), bowtie, LineString([(0.0, 0.0), (1.0, 1.0)]))
        for footprint in invalid:
            with self.subTest(geometry=footprint.wkt):
                with self.assertRaisesRegex(ValueError, "footprint"):
                    coverage_fraction(aoi, footprint)
        with self.assertRaisesRegex(ValueError, "AOI"):
            coverage_fraction(Polygon(), aoi)

    def test_summarizes_union_per_track_and_ranks_coverage_then_track(self) -> None:
        aoi = box(0.0, 0.0, 10.0, 10.0)
        granules = (
            _granule("track-2-left", 2, 21, box(0.0, 0.0, 6.0, 10.0)),
            _granule("track-1", 1, 11, box(0.0, 0.0, 6.0, 10.0)),
            _granule("track-2-right", 2, 22, box(4.0, 0.0, 10.0, 10.0)),
            _granule("track-4", 4, 41, box(0.0, 0.0, 2.0, 10.0)),
            _granule("track-3", 3, 31, box(0.0, 0.0, 2.0, 10.0)),
        )

        summaries = summarize_tracks(granules, aoi)

        self.assertEqual(tuple(item.track for item in summaries), (2, 1, 3, 4))
        self.assertAlmostEqual(summaries[0].coverage_fraction, 1.0, places=4)
        self.assertAlmostEqual(summaries[1].coverage_fraction, 0.6, places=2)
        self.assertEqual(summaries[0].frames, (21, 22))
        self.assertEqual(summaries[0].granule_count, 2)
        self.assertEqual(summaries[0].granule_ids, ("track-2-left", "track-2-right"))

    def test_rejects_invalid_granule_wkt_before_ranking(self) -> None:
        granule = _granule("broken", 1, 1, box(0.0, 0.0, 1.0, 1.0))
        object.__setattr__(granule, "footprint_wkt", "not wkt")

        with self.assertRaisesRegex(ValueError, "broken.*footprint"):
            summarize_tracks((granule,), box(0.0, 0.0, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
