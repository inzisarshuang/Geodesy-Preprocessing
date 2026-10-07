"""Tests for DEM download extents."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.dem.extent import (
    BoundingBox,
    bbox_from_config,
    bbox_from_kml,
    estimate_bbox_area_km2,
    expand_bbox_km,
)


class DemExtentTests(unittest.TestCase):
    """Verify KML selection, bounds validation, and geographic buffering."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.unique_kml = self.root / "unique.kml"
        self.unique_kml.write_text(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Placemark><name>target</name><Polygon><outerBoundaryIs><LinearRing>
            <coordinates>-5,40,0 -4,40,0 -4,41,0 -5,41,0 -5,40,0</coordinates>
            </LinearRing></outerBoundaryIs></Polygon></Placemark>
            <Placemark><name>unrelated</name><Point><coordinates>10,20,0</coordinates></Point></Placemark>
            </Document></kml>""",
            encoding="utf-8",
        )
        self.duplicate_kml = self.root / "duplicate.kml"
        self.duplicate_kml.write_text(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Placemark><name>target</name><Point><coordinates>-5,40,0</coordinates></Point></Placemark>
            <Placemark><name>target</name><Point><coordinates>-4,41,0</coordinates></Point></Placemark>
            </Document></kml>""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selects_one_named_placemark_and_rejects_zero_or_two(self) -> None:
        self.assertEqual(
            bbox_from_kml(self.unique_kml, "target"),
            BoundingBox(-5.0, 40.0, -4.0, 41.0),
        )
        with self.assertRaisesRegex(ValueError, "matched 0"):
            bbox_from_kml(self.unique_kml, "missing")
        with self.assertRaisesRegex(ValueError, "matched 2"):
            bbox_from_kml(self.duplicate_kml, "target")

    def test_without_name_unions_all_coordinates(self) -> None:
        self.assertEqual(
            bbox_from_kml(self.unique_kml),
            BoundingBox(-5.0, 20.0, 10.0, 41.0),
        )

    def test_bbox_validation_and_buffer(self) -> None:
        source = BoundingBox(-5.0, 40.0, -4.0, 41.0)
        expanded = expand_bbox_km(source, 5.0)
        self.assertLess(expanded.west, source.west)
        self.assertLess(expanded.south, source.south)
        self.assertGreater(expanded.east, source.east)
        self.assertGreater(expanded.north, source.north)
        self.assertGreater(estimate_bbox_area_km2(source), 0)
        with self.assertRaises(ValueError):
            BoundingBox(-4.0, 40.0, -5.0, 41.0)
        with self.assertRaisesRegex(ValueError, "buffer"):
            expand_bbox_km(source, -1.0)

    def test_bbox_config_requires_all_numeric_bounds(self) -> None:
        self.assertEqual(
            bbox_from_config(
                {"west": "-5", "south": "40", "east": "-4", "north": "41"}
            ),
            BoundingBox(-5.0, 40.0, -4.0, 41.0),
        )
        with self.assertRaisesRegex(ValueError, "north"):
            bbox_from_config({"west": "-5", "south": "40", "east": "-4"})


if __name__ == "__main__":
    unittest.main()
