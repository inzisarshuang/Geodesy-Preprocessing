"""Tests for Sentinel-1 burst-table domain values and IW input."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from geodesy_preprocessing.sar.acquisition.sentinel1.burst_table import (
    BurstNumberRow,
    BurstSelection,
    build_reference_burst_table,
    full_burst_roi,
    load_roi,
    read_burst_number_table,
    read_iw_file,
    select_bursts,
    write_burst_itab,
    write_reference_burst_table,
)


class BurstModelTests(unittest.TestCase):
    """Catch invalid one-based swaths, intervals, and source rows."""

    def test_valid_models_are_immutable(self) -> None:
        selection = BurstSelection(swath=2, first_burst=3, last_burst=5)
        source = BurstNumberRow(
            swath=2,
            number_of_bursts=3,
            first_burst=100.25,
            last_burst=102.25,
        )

        self.assertEqual(selection, BurstSelection(2, 3, 5))
        self.assertEqual(source.number_of_bursts, 3)
        with self.assertRaises(FrozenInstanceError):
            selection.swath = 1  # type: ignore[misc]

    def test_invalid_swath_and_interval_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "swath"):
            BurstSelection(swath=0, first_burst=1, last_burst=1)
        with self.assertRaisesRegex(ValueError, "interval"):
            BurstSelection(swath=1, first_burst=4, last_burst=3)

    def test_invalid_source_row_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "number_of_bursts"):
            BurstNumberRow(1, 0, 100.0, 100.0)
        with self.assertRaisesRegex(ValueError, "finite"):
            BurstNumberRow(1, 1, float("nan"), 100.0)


class IWInputTests(unittest.TestCase):
    """Catch malformed or non-finite GAMMA burst-location arrays."""

    def test_flat_values_are_normalized_to_nine_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "IW_1.txt"
            np.savetxt(path, np.arange(18, dtype=float)[None, :])

            rows = read_iw_file(path)

            self.assertEqual(rows.shape, (2, 9))
            np.testing.assert_array_equal(rows[1], np.arange(9, 18, dtype=float))

    def test_value_count_must_be_positive_and_divisible_by_nine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "IW_1.txt"
            path.write_text("1 2 3\n", encoding="ascii")

            with self.assertRaisesRegex(ValueError, "divisible by 9"):
                read_iw_file(path)

    def test_values_must_be_finite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "IW_1.txt"
            path.write_text("1 2 3 4 5 6 7 8 nan\n", encoding="ascii")

            with self.assertRaisesRegex(ValueError, "finite"):
                read_iw_file(path)


class ROIParsingTests(unittest.TestCase):
    """Catch coordinate-order, format, hole, validity, and bbox regressions."""

    def test_txt_coordinates_are_longitude_then_latitude(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "roi.txt"
            path.write_text("0 0\n2 0\n2 2\n0 2\n0 0\n", encoding="ascii")

            roi = load_roi(path)

            self.assertEqual(roi.bounds, (0.0, 0.0, 2.0, 2.0))
            self.assertEqual(roi.area, 4.0)

    def test_kml_uses_the_first_polygon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "roi.kml"
            path.write_text(
                "<kml xmlns='http://www.opengis.net/kml/2.2'><Document>"
                "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
                "10,20,0 11,20,0 11,21,0 10,21,0 10,20,0"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
                "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
                "30,40,0 31,40,0 31,41,0 30,41,0 30,40,0"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
                "</Document></kml>",
                encoding="utf-8",
            )

            self.assertEqual(load_roi(path).bounds, (10.0, 20.0, 11.0, 21.0))

    def test_geojson_polygon_preserves_holes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "roi.geojson"
            path.write_text(
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
                            [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]],
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(load_roi(path).area, 12.0)

    def test_geojson_feature_collection_unions_multipolygons(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "roi.json"
            path.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {},
                                "geometry": {
                                    "type": "MultiPolygon",
                                    "coordinates": [
                                        [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                                        [[[2, 0], [3, 0], [3, 1], [2, 1], [2, 0]]],
                                    ],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            roi = load_roi(path)

            self.assertEqual(roi.geom_type, "MultiPolygon")
            self.assertEqual(roi.area, 2.0)

    def test_invalid_polygon_is_rejected_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "invalid.txt"
            path.write_text("0 0\n2 2\n0 2\n2 0\n0 0\n", encoding="ascii")

            with self.assertRaisesRegex(ValueError, "valid"):
                load_roi(path)

    def test_full_burst_roi_spans_every_available_swath(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            iw1 = root / "IW_1.txt"
            iw3 = root / "IW_3.txt"
            np.savetxt(iw1, [[1, 1, -4, 1, 2, 4, 2, 4, -4]])
            np.savetxt(iw3, [[1, 6, 5, 6, 8, 9, 8, 9, 5]])

            roi = full_burst_roi({1: iw1, 3: iw3})

            self.assertEqual(roi.bounds, (-4.0, 1.0, 8.0, 9.0))

    def test_full_burst_roi_requires_at_least_one_swath(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            full_burst_roi({})


class BurstSelectionTests(unittest.TestCase):
    """Catch zero-area contact, index-origin, and swath-order regressions."""

    @staticmethod
    def _write_two_bursts(path: Path, longitude_offset: float = 0.0) -> None:
        np.savetxt(
            path,
            [
                [
                    1,
                    0,
                    longitude_offset,
                    0,
                    longitude_offset + 1,
                    1,
                    longitude_offset + 1,
                    1,
                    longitude_offset,
                ],
                [
                    2,
                    0,
                    longitude_offset + 1,
                    0,
                    longitude_offset + 2,
                    1,
                    longitude_offset + 2,
                    1,
                    longitude_offset + 1,
                ],
            ],
        )

    def test_selection_is_one_based_and_sorted_by_swath(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            iw1 = root / "IW_1.txt"
            iw3 = root / "IW_3.txt"
            self._write_two_bursts(iw1)
            self._write_two_bursts(iw3)
            roi = load_roi(self._write_roi(root / "roi.txt", 0.25, 0.75))

            selections = select_bursts({3: iw3, 1: iw1}, roi)

            self.assertEqual(
                selections,
                (BurstSelection(1, 1, 1), BurstSelection(3, 1, 1)),
            )

    def test_boundary_only_contact_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            iw1 = root / "IW_1.txt"
            self._write_two_bursts(iw1)
            roi = load_roi(self._write_roi(root / "roi.txt", 1.0, 1.5))

            self.assertEqual(select_bursts({1: iw1}, roi), (BurstSelection(1, 2, 2),))

    def test_no_overlap_fails_instead_of_publishing_empty_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            iw1 = root / "IW_1.txt"
            self._write_two_bursts(iw1)
            roi = load_roi(self._write_roi(root / "roi.txt", 10.0, 11.0))

            with self.assertRaisesRegex(ValueError, "no bursts overlap"):
                select_bursts({1: iw1}, roi)

    @staticmethod
    def _write_roi(path: Path, minimum_x: float, maximum_x: float) -> Path:
        path.write_text(
            f"{minimum_x} 0.25\n{maximum_x} 0.25\n"
            f"{maximum_x} 0.75\n{minimum_x} 0.75\n{minimum_x} 0.25\n",
            encoding="ascii",
        )
        return path


class BurstNumberTableTests(unittest.TestCase):
    """Catch field loss, ambiguous fallback, and missing-IW row shifts."""

    def test_structured_rows_preserve_real_iw_labels_and_sort_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "Old_burst_number_table_060"
            path.write_text(
                "zipfile: ref.zip\n"
                "iw3_number_of_bursts: 5\n"
                "iw3_first_burst: 300.5\n"
                "iw3_last_burst: 304.5\n"
                "iw2_number_of_bursts: 4\n"
                "iw2_first_burst: 200.5\n"
                "iw2_last_burst: 203.5\n",
                encoding="ascii",
            )

            rows = read_burst_number_table(path)

            self.assertEqual(list(rows), [2, 3])
            self.assertEqual(rows[2], BurstNumberRow(2, 4, 200.5, 203.5))

    def test_incomplete_structured_row_names_the_swath(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "Old_burst_number_table"
            path.write_text(
                "iw2_number_of_bursts: 4\niw2_first_burst: 200.5\n",
                encoding="ascii",
            )

            with self.assertRaisesRegex(ValueError, "swath 2"):
                read_burst_number_table(path)

    def test_numeric_fallback_reconstructs_three_iw_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "Old_burst_number_table"
            path.write_text(
                "3 100.5 102.5 4 200.5 203.5 5 300.5 304.5\n",
                encoding="ascii",
            )

            rows = read_burst_number_table(path)

            self.assertEqual(rows[1], BurstNumberRow(1, 3, 100.5, 102.5))
            self.assertEqual(rows[3], BurstNumberRow(3, 5, 300.5, 304.5))

    def test_ambiguous_numeric_fallback_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "Old_burst_number_table"
            path.write_text("0 1 2 3 4 5 6 7 8 9\n", encoding="ascii")

            with self.assertRaisesRegex(ValueError, "exactly 9"):
                read_burst_number_table(path)

    def test_reference_calculation_uses_explicit_missing_swath_key(self) -> None:
        selection = (BurstSelection(swath=2, first_burst=2, last_burst=3),)
        source = {2: BurstNumberRow(2, 8, 220.5, 227.5)}

        self.assertEqual(
            build_reference_burst_table(selection, source),
            (BurstNumberRow(2, 2, 221.5, 222.5),),
        )

    def test_reference_calculation_names_absent_source_swath(self) -> None:
        with self.assertRaisesRegex(ValueError, "swath 2"):
            build_reference_burst_table(
                (BurstSelection(2, 1, 1),),
                {3: BurstNumberRow(3, 2, 300.0, 301.0)},
            )


class AtomicWriterTests(unittest.TestCase):
    """Catch output-format drift and replacement of valid files by partial data."""

    def test_writers_preserve_legacy_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            itab = write_burst_itab(
                (BurstSelection(1, 2, 4), BurstSelection(3, 1, 1)),
                root / "burst_itab",
            )
            reference = write_reference_burst_table(
                (BurstNumberRow(1, 3, 101.5, 103.5),),
                root / "ref_burst_number_table",
            )

            self.assertEqual(itab.read_bytes(), b"1 2 4\n3 1 1\n")
            self.assertEqual(
                reference.read_bytes(),
                b"zipfile:    ref.zip\n"
                b"iw1_number_of_bursts: 3\n"
                b"iw1_first_burst:      101.500000\n"
                b"iw1_last_burst:       103.500000\n",
            )

    def test_failed_replace_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "burst_itab"
            destination.write_bytes(b"existing\n")

            with patch(
                "geodesy_preprocessing.sar.acquisition.sentinel1."
                "burst_table.os.replace",
                side_effect=OSError("injected replace failure"),
            ):
                with self.assertRaisesRegex(OSError, "injected"):
                    write_burst_itab((BurstSelection(1, 1, 1),), destination)

            self.assertEqual(destination.read_bytes(), b"existing\n")
            self.assertEqual(
                [path for path in root.iterdir() if path != destination],
                [],
            )


if __name__ == "__main__":
    unittest.main()
