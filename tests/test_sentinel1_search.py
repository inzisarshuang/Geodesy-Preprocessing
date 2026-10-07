"""Tests for ASF Sentinel-1 search normalization and local polarization filtering."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.acquisition.sentinel1.models import (
    Sentinel1AcquisitionCase,
    Sentinel1ProductType,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.search import (
    AsfSentinel1SearchClient,
    search_sentinel1,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sentinel1_search_results.json"


class _Client:
    def __init__(self, records):
        self.records = records

    def search(self, case):
        return self.records


class Sentinel1SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        root = FIXTURE.parent
        self.case = Sentinel1AcquisitionCase(
            name="main", project_root=root, kml_file=root / "area.kml", bbox=None,
            start_date="2026-09-08", end_date="2026-09-10",
            product_type=Sentinel1ProductType.SLC, beam_mode="IW",
            polarizations=("VV",), provider="asf", tracks=None,
            orbit_direction=None, coverage_threshold=0.999,
            query_dir=root / "QUERY", download_dir=root / "INPUT",
            download_threads=2, max_retries=3, retry_delay_seconds=1,
            keep_burst_files=True,
        )

    def test_normalizes_slc_burst_and_grd_records(self) -> None:
        for product_type, index in (
            (Sentinel1ProductType.SLC, 0),
            (Sentinel1ProductType.BURST, 1),
            (Sentinel1ProductType.GRD, 2),
        ):
            with self.subTest(product_type=product_type):
                item = search_sentinel1(
                    replace(self.case, product_type=product_type),
                    _Client([copy.deepcopy(self.records[index])]),
                )[0]
                self.assertIs(item.product_type, product_type)
                self.assertEqual(item.relative_orbit, 154)
                self.assertEqual(item.orbit_direction, "DESCENDING")
                self.assertIn("VV", item.polarizations)
        burst = search_sentinel1(
            replace(self.case, product_type=Sentinel1ProductType.BURST),
            _Client([copy.deepcopy(self.records[1])]),
        )[0]
        self.assertEqual((burst.swath, burst.burst_index), ("IW2", 7))
        self.assertEqual(burst.full_burst_id, "154_IW2_391")
        self.assertEqual(burst.merge_group, "S1A_154_20260908")

    def test_burst_adapter_uses_the_dedicated_slc_burst_dataset(self) -> None:
        case = replace(
            self.case,
            product_type=Sentinel1ProductType.BURST,
            kml_file=None,
            bbox=(-5.0, 38.0, -1.0, 42.0),
        )
        with mock.patch("asf_search.geo_search", return_value=[self.records[1]]) as call:
            AsfSentinel1SearchClient().search(case)
        self.assertEqual(call.call_args.kwargs["dataset"], ["SLC-BURST"])

    def test_rejects_malformed_or_incomplete_records_with_identity(self) -> None:
        required = ("startTime", "pathNumber", "orbit", "url", "bytes")
        for key in required:
            record = copy.deepcopy(self.records[0])
            del record["properties"][key]
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "S1A_IW_SLC.*" + key):
                search_sentinel1(self.case, _Client([record]))
        record = copy.deepcopy(self.records[0])
        record["geometry"] = {"type": "Point", "coordinates": [-3, 40]}
        with self.assertRaisesRegex(ValueError, "S1A_IW_SLC.*footprint"):
            search_sentinel1(self.case, _Client([record]))

    def test_deduplicates_by_product_identity_and_sorts_deterministically(self) -> None:
        records = [copy.deepcopy(self.records[0]), copy.deepcopy(self.records[0])]
        with self.assertRaisesRegex(ValueError, "duplicate.*S1A_IW_SLC"):
            search_sentinel1(self.case, _Client(records))

    def test_sentinel1_search_filters_polarization_locally_as_subset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kml = root / "a.kml"
            kml.write_text("""<kml><Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>-4,39 -2,39 -2,41 -4,41 -4,39</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>""", encoding="utf-8")
            case = replace(self.case, project_root=root, kml_file=kml, query_dir=root / "Q", download_dir=root / "D", tracks=(154,), orbit_direction="DESCENDING")
            with mock.patch("asf_search.geo_search", return_value=[self.records[0]]) as call:
                records = AsfSentinel1SearchClient().search(case)
        self.assertEqual(len(records), 1)
        options = call.call_args.kwargs
        self.assertNotIn("polarization", options)
        self.assertEqual(options["relativeOrbit"], [154])
        self.assertEqual(options["flightDirection"], "DESCENDING")
        self.assertIn("POLYGON", options["intersectsWith"])
        normalized = search_sentinel1(case, _Client(records))
        self.assertEqual(normalized[0].polarizations, ("VH", "VV"))


if __name__ == "__main__":
    unittest.main()
