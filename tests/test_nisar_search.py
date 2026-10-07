"""Tests for normalizing provider-specific NISAR search records."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from shapely import wkt
from shapely.geometry import shape

from geodesy_preprocessing.sar.acquisition.nisar.models import (
    NisarFrequencyMode,
    NisarProductType,
    NisarSearchCase,
)
from geodesy_preprocessing.sar.acquisition.nisar.search import AsfSearchClient, search_nisar


FIXTURE = Path(__file__).parent / "fixtures" / "nisar_search_results.json"


class _FixtureClient:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records
        self.received_case: NisarSearchCase | None = None

    def search(self, case: NisarSearchCase) -> list[dict[str, object]]:
        self.received_case = case
        return self.records


class NisarSearchTests(unittest.TestCase):
    """Catch provider field drift before query artifacts are published."""

    def setUp(self) -> None:
        self.records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        root = FIXTURE.parent
        self.case = NisarSearchCase(
            name="main",
            project_root=root,
            kml_file=root / "area.kml",
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
            query_dir=root / "QUERY",
            download_dir=root / "INPUT",
        )

    def test_normalizes_complete_asf_records_and_orders_by_time_then_identity(self) -> None:
        client = _FixtureClient(self.records)

        granules = search_nisar(self.case, client)

        self.assertIs(client.received_case, self.case)
        self.assertEqual(
            tuple(item.granule_id for item in granules),
            (
                "NISAR_L1_PR_RSLC_20260105T091500_042_006",
                "NISAR_L1_PR_RSLC_20260105T102030_087_022",
                "NISAR_L1_PR_RSLC_20260203T102030_087_023",
            ),
        )
        first = granules[0]
        self.assertEqual(first.acquisition_date, "2026-01-05")
        self.assertIs(first.product_type, NisarProductType.RSLC)
        self.assertEqual(first.start_time, "2026-01-05T09:15:00Z")
        self.assertEqual(first.end_time, "2026-01-05T09:15:32Z")
        self.assertEqual((first.track, first.frame), (42, 6))
        self.assertEqual(first.orbit_direction, "ASCENDING")
        self.assertEqual(first.size_bytes, 1_800_000_000)
        self.assertEqual(first.frequencies, ("A",))
        self.assertEqual(first.polarizations, ("VV",))
        self.assertIsNone(first.checksum)
        self.assertTrue(wkt.loads(first.footprint_wkt).equals(shape(self.records[2]["geometry"])))
        second = granules[1]
        self.assertEqual(second.frequencies, ("A", "B"))
        self.assertEqual(second.polarizations, ("HH", "HV"))
        self.assertIsNone(second.checksum)
        self.assertEqual(granules[2].checksum, "md5:cccc")

    def test_rejects_duplicate_granule_names(self) -> None:
        records = copy.deepcopy(self.records)
        records.append(copy.deepcopy(records[0]))

        with self.assertRaisesRegex(ValueError, "duplicate.*NISAR_L1"):
            search_nisar(self.case, _FixtureClient(records))

    def test_rejects_each_missing_required_property(self) -> None:
        required = (
            "sceneName",
            "startTime",
            "stopTime",
            "pathNumber",
            "frameNumber",
            "flightDirection",
            "bytes",
            "url",
            "mainBandPolarization",
        )
        for key in required:
            with self.subTest(key=key):
                records = [copy.deepcopy(self.records[0])]
                del records[0]["properties"][key]
                if key == "sceneName":
                    del records[0]["properties"]["fileID"]
                with self.assertRaisesRegex(ValueError, key):
                    search_nisar(self.case, _FixtureClient(records))

    def test_rejects_missing_or_non_polygon_footprint(self) -> None:
        missing = [copy.deepcopy(self.records[0])]
        del missing[0]["geometry"]
        with self.assertRaisesRegex(ValueError, "geometry"):
            search_nisar(self.case, _FixtureClient(missing))

        point = [copy.deepcopy(self.records[0])]
        point[0]["geometry"] = {"type": "Point", "coordinates": [-3.0, 40.0]}
        with self.assertRaisesRegex(ValueError, "Polygon"):
            search_nisar(self.case, _FixtureClient(point))

    def test_asf_adapter_leaves_polarization_subset_filtering_to_local_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kml = root / "area.kml"
            kml.write_text(
                """<kml><Placemark><Polygon><outerBoundaryIs><LinearRing>
                <coordinates>-4,39 -2,39 -2,41 -4,41 -4,39</coordinates>
                </LinearRing></outerBoundaryIs></Polygon></Placemark></kml>""",
                encoding="utf-8",
            )
            case = NisarSearchCase(
                name="main",
                project_root=root,
                kml_file=kml,
                start_date="2026-01-01",
                end_date="2026-03-01",
                product_type=NisarProductType.RSLC,
                band="L",
                polarizations=("HH", "HV"),
                provider="asf",
                frequency_mode=NisarFrequencyMode.MAIN_ONLY,
                tracks=(87,),
                frames=(22, 23),
                orbit_direction="DESCENDING",
                query_dir=root / "QUERY",
                download_dir=root / "INPUT",
            )
            record = copy.deepcopy(self.records[0])
            with mock.patch("asf_search.geo_search", return_value=[record]) as geo_search:
                result = AsfSearchClient().search(case)

        self.assertEqual(result, (record,))
        options = geo_search.call_args.kwargs
        self.assertEqual(options["dataset"], ["NISAR"])
        self.assertEqual(options["processingLevel"], ["RSLC"])
        self.assertEqual(options["start"], "2026-01-01")
        self.assertEqual(options["end"], "2026-03-01")
        self.assertEqual(options["relativeOrbit"], [87])
        self.assertEqual(options["frame"], [22, 23])
        self.assertEqual(options["flightDirection"], "DESCENDING")
        self.assertNotIn("mainBandPolarization", options)
        self.assertIn("POLYGON", options["intersectsWith"])

    def test_gslc_search_selects_primary_hdf5_file(self) -> None:
        record = copy.deepcopy(self.records[0])
        properties = record["properties"]
        granule_id = "NISAR_L2_PR_GSLC_030_087_A_022_TEST"
        science_name = f"{granule_id}.h5"
        properties.update(
            {
                "sceneName": granule_id,
                "fileID": granule_id,
                "processingLevel": "GSLC",
                "url": f"https://example.test/{science_name}",
                "bytes": {
                    science_name: {"bytes": 11_400_118_272, "format": "HDF5"},
                    f"{granule_id}_QA_STATS.h5": {
                        "bytes": 98_185,
                        "format": "HDF5",
                    },
                },
            }
        )
        case = replace(self.case, product_type=NisarProductType.GSLC)

        granule = search_nisar(case, _FixtureClient([record]))[0]

        self.assertIs(granule.product_type, NisarProductType.GSLC)
        self.assertEqual(granule.size_bytes, 11_400_118_272)


if __name__ == "__main__":
    unittest.main()
