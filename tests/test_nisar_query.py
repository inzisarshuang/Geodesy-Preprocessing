"""Tests for transactional NISAR query products and stale-query blocking."""

from __future__ import annotations

import copy
import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from geodesy_preprocessing.sar.acquisition.nisar.manifest import (
    query_fingerprint,
    validate_query_for_download,
)
from geodesy_preprocessing.sar.acquisition.nisar.models import (
    NisarFrequencyMode,
    NisarProductType,
    NisarSearchCase,
)
from geodesy_preprocessing.sar.acquisition.nisar.query import run_nisar_query


FIXTURE = Path(__file__).parent / "fixtures" / "nisar_search_results.json"
KML = """<kml><Placemark><Polygon><outerBoundaryIs><LinearRing>
<coordinates>-5,38 -1,38 -1,42 -5,42 -5,38</coordinates>
</LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"""


class _QueryOnlyClient:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def search(self, case: NisarSearchCase) -> list[dict[str, object]]:
        return self.records

    def download(self, *args, **kwargs) -> None:
        raise AssertionError("query mode must not call download")


class NisarQueryTests(unittest.TestCase):
    """Ensure human selection artifacts cannot silently drift before download."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kml = self.root / "area.kml"
        self.kml.write_text(KML, encoding="utf-8")
        self.records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.records[2]["properties"]["mainBandPolarization"] = ["HH", "HV"]
        self.case = NisarSearchCase(
            name="main",
            project_root=self.root,
            kml_file=self.kml,
            start_date="2026-01-01",
            end_date="2026-03-01",
            product_type=NisarProductType.RSLC,
            band="L",
            polarizations=("HH", "HV"),
            provider="asf",
            frequency_mode=NisarFrequencyMode.MAIN_ONLY,
            tracks=None,
            frames=None,
            orbit_direction=None,
            query_dir=self.root / "QUERY" / "NISAR",
            download_dir=self.root / "INPUT" / "NISAR",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_auto_tracks_publishes_all_results_deterministically_without_download(self) -> None:
        result = run_nisar_query(self.case, _QueryOnlyClient(copy.deepcopy(self.records)))

        self.assertEqual(len(result.all_granules), 3)
        self.assertEqual(result.selected_granules, result.all_granules)
        expected = {
            "NISAR_RSLC_all.csv",
            "NISAR_RSLC_filtered.csv",
            "NISAR_RSLC_footprints_all.png",
            "NISAR_RSLC_footprints_selected.png",
            "NISAR_RSLC_track_coverage.csv",
            "NISAR_RSLC_download_urls_track042.txt",
            "NISAR_RSLC_download_urls_track087.txt",
            "query_manifest.json",
        }
        self.assertEqual({path.name for path in self.case.query_dir.iterdir()}, expected)
        with (self.case.query_dir / "NISAR_RSLC_all.csv").open(
            newline="", encoding="utf-8"
        ) as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(
            rows[0],
            [
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
            ],
        )
        self.assertEqual(
            [row[0] for row in rows[1:]],
            [
                "NISAR_L1_PR_RSLC_20260105T091500_042_006",
                "NISAR_L1_PR_RSLC_20260105T102030_087_022",
                "NISAR_L1_PR_RSLC_20260203T102030_087_023",
            ],
        )
        manifest_text = (self.case.query_dir / "query_manifest.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(manifest_text, json.dumps(json.loads(manifest_text), indent=2, sort_keys=True) + "\n")
        validate_query_for_download(self.case, result.manifest)

    def test_manual_track_frame_frequency_and_polarization_filters(self) -> None:
        case = replace(
            self.case,
            tracks=(87,),
            frames=(22, 23),
            frequency_mode=NisarFrequencyMode.MAIN_PLUS_AUXILIARY,
        )

        result = run_nisar_query(case, _QueryOnlyClient(copy.deepcopy(self.records)))

        self.assertEqual(
            tuple(item.granule_id for item in result.selected_granules),
            ("NISAR_L1_PR_RSLC_20260105T102030_087_022",),
        )
        urls = case.query_dir / "NISAR_RSLC_download_urls_track087.txt"
        self.assertEqual(
            urls.read_text(encoding="utf-8"),
            "https://example.test/NISAR_20260105_087_022.h5\n",
        )

        vv_case = replace(case, polarizations=("VV",))
        vv_result = run_nisar_query(vv_case, _QueryOnlyClient(copy.deepcopy(self.records)))
        self.assertEqual(vv_result.selected_granules, ())

    def test_query_fingerprint_is_stable_and_covers_every_search_option(self) -> None:
        digest = "a" * 64
        first = query_fingerprint(self.case, digest)
        self.assertEqual(first, query_fingerprint(self.case, digest))
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, query_fingerprint(replace(self.case, tracks=(87,)), digest))
        self.assertNotEqual(first, query_fingerprint(self.case, "b" * 64))
        self.assertNotEqual(
            first,
            query_fingerprint(
                replace(self.case, frequency_mode=NisarFrequencyMode.MAIN_PLUS_AUXILIARY),
                digest,
            ),
        )

    def test_download_rejects_stale_query_fingerprint(self) -> None:
        result = run_nisar_query(self.case, _QueryOnlyClient(copy.deepcopy(self.records)))
        self.kml.write_text(KML.replace("-5,38", "-5.1,38"), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "stale.*KML|fingerprint"):
            validate_query_for_download(self.case, result.manifest)

    def test_download_rejects_modified_filtered_csv(self) -> None:
        result = run_nisar_query(self.case, _QueryOnlyClient(copy.deepcopy(self.records)))
        filtered = self.case.query_dir / "NISAR_RSLC_filtered.csv"
        filtered.write_text(filtered.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "filtered CSV"):
            validate_query_for_download(self.case, result.manifest)

    def test_query_artifacts_are_isolated_by_product_type(self) -> None:
        records = copy.deepcopy(self.records)
        for record in records:
            record["properties"]["processingLevel"] = "GSLC"
        case = replace(
            self.case,
            product_type=NisarProductType.GSLC,
            query_dir=self.root / "QUERY" / "GSLC",
        )

        result = run_nisar_query(case, _QueryOnlyClient(records))

        self.assertTrue((case.query_dir / "NISAR_GSLC_all.csv").is_file())
        self.assertTrue((case.query_dir / "NISAR_GSLC_filtered.csv").is_file())
        self.assertTrue(
            (case.query_dir / "NISAR_GSLC_download_urls_track087.txt").is_file()
        )
        self.assertTrue(all(item.product_type is NisarProductType.GSLC for item in result.all_granules))
        self.assertFalse(any("RSLC" in path.name for path in case.query_dir.iterdir()))

    def test_download_rejects_query_from_other_product_type(self) -> None:
        result = run_nisar_query(
            self.case, _QueryOnlyClient(copy.deepcopy(self.records))
        )
        gslc_case = replace(self.case, product_type=NisarProductType.GSLC)

        with self.assertRaisesRegex(ValueError, "stale.*fingerprint|fingerprint"):
            validate_query_for_download(gslc_case, result.manifest)


if __name__ == "__main__":
    unittest.main()
