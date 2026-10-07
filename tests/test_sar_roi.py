"""Tests for backend-neutral polygonal SAR regions of interest."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.ops import transform

from geodesy_preprocessing.sar.common.roi import (
    buffer_wgs84_geometry,
    load_kml_roi,
)


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>main</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
    -5.0,40.0 -4.9,40.0 -4.9,40.1 -5.0,40.1 -5.0,40.0
  </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Placemark><name>two parts</name><MultiGeometry>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      -4.8,40.0 -4.7,40.0 -4.7,40.1 -4.8,40.1 -4.8,40.0
    </coordinates></LinearRing></outerBoundaryIs></Polygon>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      -4.6,40.0 -4.5,40.0 -4.5,40.1 -4.6,40.1 -4.6,40.0
    </coordinates></LinearRing></outerBoundaryIs></Polygon>
  </MultiGeometry></Placemark>
</Document></kml>
"""


class SarRoiTests(unittest.TestCase):
    """Load named KML polygons and apply a real metric buffer."""

    def _kml(self, directory: str, contents: str = KML) -> Path:
        path = Path(directory) / "aoi.kml"
        path.write_text(contents, encoding="utf-8")
        return path

    def test_loads_all_polygonal_placemarks_and_named_multigeometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self._kml(directory)
            all_geometry = load_kml_roi(source, None)
            selected = load_kml_roi(source, "two parts")

        self.assertEqual(all_geometry.geom_type, "MultiPolygon")
        self.assertAlmostEqual(all_geometry.area, 0.03, places=8)
        self.assertEqual(selected.geom_type, "MultiPolygon")
        self.assertAlmostEqual(selected.area, 0.02, places=8)

    def test_buffer_is_five_kilometres_in_local_metric_crs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            geometry = load_kml_roi(self._kml(directory), "main")
        buffered = buffer_wgs84_geometry(geometry, 5000.0)
        transformer = Transformer.from_crs(
            CRS.from_epsg(4326), CRS.from_epsg(32630), always_xy=True
        )
        metric = transform(transformer.transform, geometry)
        buffered_metric = transform(transformer.transform, buffered)

        self.assertTrue(buffered.contains(geometry))
        self.assertGreater(buffered_metric.boundary.distance(metric.boundary), 4950.0)
        self.assertLess(buffered_metric.boundary.distance(metric.boundary), 5050.0)

    def test_rejects_empty_missing_placemark_and_antimeridian_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            empty = self._kml(directory, "<kml><Document/></kml>")
            with self.assertRaisesRegex(ValueError, "Polygon"):
                load_kml_roi(empty, None)

            source = self._kml(directory, KML)
            with self.assertRaisesRegex(ValueError, "missing"):
                load_kml_roi(source, "missing")

            crossing = self._kml(
                directory,
                """<kml><Placemark><Polygon><outerBoundaryIs><LinearRing>
                <coordinates>179,10 -179,10 -179,11 179,11 179,10</coordinates>
                </LinearRing></outerBoundaryIs></Polygon></Placemark></kml>""",
            )
            with self.assertRaisesRegex(ValueError, "antimeridian"):
                load_kml_roi(crossing, None)


if __name__ == "__main__":
    unittest.main()
