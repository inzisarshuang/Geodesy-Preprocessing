"""Tests for transactional Sentinel-1 query products and stale-input blocking."""

from __future__ import annotations

import copy
import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from geodesy_preprocessing.sar.acquisition.sentinel1.manifest import (
    query_fingerprint,
    validate_query_for_download,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.models import (
    Sentinel1AcquisitionCase,
    Sentinel1ProductType,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.query import (
    load_query_for_download,
    publish_sentinel1_query,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sentinel1_search_results.json"
KML = """<kml><Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>-5,38 -1,38 -1,42 -5,42 -5,38</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"""


class _Client:
    def __init__(self, records):
        self.records = records

    def search(self, case):
        return self.records


class Sentinel1QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kml = self.root / "area.kml"
        self.kml.write_text(KML, encoding="utf-8")
        self.record = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
        self.case = Sentinel1AcquisitionCase(
            name="main", project_root=self.root, kml_file=self.kml, bbox=None,
            start_date="2026-09-08", end_date="2026-09-10",
            product_type=Sentinel1ProductType.SLC, beam_mode="IW",
            polarizations=("VV",), provider="asf", tracks=(154,),
            orbit_direction="DESCENDING", coverage_threshold=0.999,
            query_dir=self.root / "QUERY" / "SLC", download_dir=self.root / "INPUT",
            download_threads=2, max_retries=3, retry_delay_seconds=1,
            keep_burst_files=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_publishes_exact_artifacts_images_and_loadable_selected_products(self) -> None:
        result = publish_sentinel1_query(self.case, _Client([copy.deepcopy(self.record)]))
        expected = {
            "S1_SLC_all.csv", "S1_SLC_selected.csv",
            "S1_SLC_coverage_all.png", "S1_SLC_coverage_selected.png",
            "S1_SLC_track_coverage.csv", "S1_SLC_download_urls_track154.txt",
            "images_154", "query_manifest.json",
        }
        self.assertEqual({item.name for item in self.case.query_dir.iterdir()}, expected)
        self.assertEqual((self.case.query_dir / "images_154").read_text(), "20260908\n")
        self.assertEqual(len(result.selected_products), 1)
        loaded = load_query_for_download(self.case)
        self.assertEqual(loaded.selected_products[0].product_id, result.selected_products[0].product_id)
        with (self.case.query_dir / "S1_SLC_track_coverage.csv").open(newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["track", "dates", "product_count", "minimum_date_coverage", "product_ids"])

    def test_query_fingerprint_is_stable_and_product_type_isolated(self) -> None:
        digest = "a" * 64
        first = query_fingerprint(self.case, digest)
        self.assertEqual(first, query_fingerprint(self.case, digest))
        self.assertNotEqual(first, query_fingerprint(replace(self.case, tracks=None), digest))
        self.assertNotEqual(first, query_fingerprint(replace(self.case, product_type=Sentinel1ProductType.GRD), digest))

    def test_modified_selected_csv_or_kml_is_rejected(self) -> None:
        result = publish_sentinel1_query(self.case, _Client([copy.deepcopy(self.record)]))
        selected = self.case.query_dir / "S1_SLC_selected.csv"
        selected.write_text(selected.read_text() + "tampered\n")
        with self.assertRaisesRegex(ValueError, "selected CSV"):
            validate_query_for_download(self.case, result.manifest)

        publish_sentinel1_query(self.case, _Client([copy.deepcopy(self.record)]))
        self.kml.write_text(KML.replace("-5,38", "-4.9,38"))
        with self.assertRaisesRegex(ValueError, "stale.*KML|fingerprint"):
            load_query_for_download(self.case)

    def test_product_type_outputs_do_not_mix(self) -> None:
        record = copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))[2])
        case = replace(self.case, product_type=Sentinel1ProductType.GRD, query_dir=self.root / "QUERY" / "GRD")
        publish_sentinel1_query(case, _Client([record]))
        self.assertTrue((case.query_dir / "S1_GRD_all.csv").is_file())
        self.assertFalse(any("SLC" in path.name for path in case.query_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
