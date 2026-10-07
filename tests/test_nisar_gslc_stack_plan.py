"""Tests for metadata-only NISAR GSLC grid and KML crop planning."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform

from geodesy_preprocessing.sar.gslc_subsetting.nisar.models import NisarGslcStackCase
from geodesy_preprocessing.sar.gslc_subsetting.nisar.plan import (
    build_nisar_gslc_stack_plan,
)
from tests.fixtures.nisar_gslc import write_gslc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NisarGslcStackPlanTests(unittest.TestCase):
    """Stop before output when identity, completeness, grid, or coverage differs."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.source_dir = self.project / "INPUT/NISAR/GSLC"
        self.source_dir.mkdir(parents=True)
        self.manifest = self.source_dir / "source_manifest.json"
        self.kml = self.project / "INPUT/ROI/area.kml"
        self.kml.parent.mkdir(parents=True)
        self.paths = {
            "20260831": self.source_dir / "NISAR_GSLC_20260831.h5",
            "20260912": self.source_dir / "NISAR_GSLC_20260912.h5",
        }
        for date, path in self.paths.items():
            iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            write_gslc(
                path,
                bands={"L": {"A": ("HH", "HV"), "B": ("HH",)}},
                granule_id=f"NISAR_L2_GSLC_{date}",
                acquisition_start=f"{iso}T10:20:30Z",
                acquisition_end=f"{iso}T10:21:02Z",
            )
        self._write_manifest()
        self._write_kml([(500021.0, 4200061.0, 500059.0, 4200079.0)])
        self.case = NisarGslcStackCase(
            name="la",
            project_root=self.project,
            source_manifest=self.manifest,
            kml_file=self.kml,
            kml_placemark=None,
            buffer_km=0.0,
            band="L",
            frequency="A",
            polarizations=("HH", "HV"),
            reference_date="20260831",
            output_dir=self.project / "WORK/NISAR/GSLC/LA/STACK",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _products(self) -> list[dict[str, object]]:
        products = []
        for date, path in self.paths.items():
            products.append(
                {
                    "granule_id": f"NISAR_L2_GSLC_{date}",
                    "acquisition_date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
                    "track": 87,
                    "frame": 22,
                    "orbit_direction": "ASCENDING",
                    "product_type": "GSLC",
                    "product_specification_version": "1.4.0",
                    "bands": ["L"],
                    "frequencies": ["A", "B"],
                    "polarizations": ["HH", "HV"],
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "status": "downloaded",
                    "file": path.name,
                }
            )
        return products

    def _write_manifest(self, products: list[dict[str, object]] | None = None) -> None:
        document = {
            "schema_version": 1,
            "case_name": "main",
            "query_fingerprint": "a" * 64,
            "products": self._products() if products is None else products,
        }
        self.manifest.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _write_kml(self, rectangles: list[tuple[float, float, float, float]]) -> None:
        reverse = Transformer.from_crs(32630, 4326, always_xy=True)
        polygons = []
        for west, south, east, north in rectangles:
            points = (
                reverse.transform(west, south),
                reverse.transform(east, south),
                reverse.transform(east, north),
                reverse.transform(west, north),
                reverse.transform(west, south),
            )
            coordinates = " ".join(f"{lon:.12f},{lat:.12f},0" for lon, lat in points)
            polygons.append(
                "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
                + coordinates
                + "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
            )
        self.kml.write_text(
            "<kml xmlns=\"http://www.opengis.net/kml/2.2\"><Document><Placemark>"
            "<name>AOI</name><MultiGeometry>"
            + "".join(polygons)
            + "</MultiGeometry></Placemark></Document></kml>\n",
            encoding="utf-8",
        )

    def test_builds_complete_date_polarization_plan_without_mutating_sources(self) -> None:
        before = {date: (path.stat(), _sha256(path)) for date, path in self.paths.items()}

        plan = build_nisar_gslc_stack_plan(self.case)

        self.assertEqual(plan.product_type, "GSLC")
        self.assertEqual(plan.product_specification_version, "1.4.0")
        self.assertEqual((plan.track, plan.frame, plan.orbit_direction), (87, 22, "ASCENDING"))
        self.assertEqual(plan.dates, ("20260831", "20260912"))
        self.assertEqual(plan.reference_date, "20260831")
        self.assertEqual(plan.selected_polarizations, ("HH", "HV"))
        self.assertEqual(plan.window.shape, (1, 2))
        self.assertEqual(
            (plan.window.row_start, plan.window.row_stop, plan.window.column_start, plan.window.column_stop),
            (1, 2, 1, 3),
        )
        self.assertEqual(
            tuple((asset.date, asset.polarization) for asset in plan.assets),
            (("20260831", "HH"), ("20260831", "HV"), ("20260912", "HH"), ("20260912", "HV")),
        )
        self.assertTrue(all(value == 1.0 for _, value in plan.roi_coverage))
        after = {date: (path.stat(), _sha256(path)) for date, path in self.paths.items()}
        self.assertEqual(before, after)
        self.assertFalse(self.case.output_dir.exists())

    def test_rejects_identity_date_reference_and_channel_incompleteness(self) -> None:
        products = self._products()
        cases = []

        wrong_type = [dict(item) for item in products]
        wrong_type[1]["product_type"] = "RSLC"
        cases.append((wrong_type, self.case, "20260912.*product_type"))

        wrong_track = [dict(item) for item in products]
        wrong_track[1]["track"] = 88
        cases.append((wrong_track, self.case, "20260912.*track"))

        duplicate_date = [dict(products[0]), dict(products[0])]
        duplicate_date[1]["file"] = products[1]["file"]
        duplicate_date[1]["sha256"] = products[1]["sha256"]
        duplicate_date[1]["size_bytes"] = products[1]["size_bytes"]
        duplicate_date[1]["granule_id"] = products[1]["granule_id"]
        cases.append((duplicate_date, self.case, "duplicate.*20260831"))

        cases.append((products, replace(self.case, reference_date="20260101"), "reference_date"))
        cases.append((products, replace(self.case, polarizations=("VV",)), "20260831.*VV"))

        for changed, case, message in cases:
            with self.subTest(message=message):
                self._write_manifest(changed)
                with self.assertRaisesRegex(ValueError, message):
                    build_nisar_gslc_stack_plan(case)
        self._write_manifest(products)

    def test_reports_date_and_field_for_every_grid_mismatch(self) -> None:
        variants = (
            ({"epsg": 32629}, "EPSG"),
            ({"x_coordinates": [500010.0, 500031.0, 500052.0, 500073.0]}, "x_spacing"),
            ({"x_coordinates": [500020.0, 500040.0, 500060.0, 500080.0]}, "x_start"),
            ({"x_coordinates": [500010.0, 500030.0, 500050.0, 500070.0, 500090.0]}, "shape"),
        )
        second = self.paths["20260912"]
        for options, field in variants:
            with self.subTest(field=field):
                write_gslc(
                    second,
                    bands={"L": {"A": ("HH", "HV"), "B": ("HH",)}},
                    granule_id="NISAR_L2_GSLC_20260912",
                    acquisition_start="2026-09-12T10:20:30Z",
                    acquisition_end="2026-09-12T10:21:02Z",
                    **options,
                )
                self._write_manifest()
                with self.assertRaisesRegex(ValueError, f"20260912.*{field}"):
                    build_nisar_gslc_stack_plan(self.case)

    def test_multipolygon_buffer_rounds_outward_to_one_deterministic_window(self) -> None:
        self._write_kml(
            [
                (500021.0, 4200061.0, 500030.0, 4200079.0),
                (500050.0, 4200061.0, 500059.0, 4200079.0),
            ]
        )
        plan = build_nisar_gslc_stack_plan(replace(self.case, buffer_km=0.005))

        self.assertEqual(plan.roi.geom_type, "MultiPolygon")
        self.assertEqual(
            (plan.window.row_start, plan.window.row_stop, plan.window.column_start, plan.window.column_stop),
            (0, 3, 0, 4),
        )
        forward = Transformer.from_crs(4326, 32630, always_xy=True)
        buffered = transform(forward.transform, plan.buffered_roi)
        pixel_bounds = box(500000.0, 4200040.0, 500080.0, 4200100.0)
        self.assertTrue(pixel_bounds.buffer(1.0e-6).covers(buffered))

    def test_gslc_plan_rejects_buffered_aoi_outside_any_scene(self) -> None:
        self._write_kml([(499990.0, 4200060.0, 500020.0, 4200080.0)])

        with self.assertRaisesRegex(ValueError, "20260831.*buffered AOI.*outside"):
            build_nisar_gslc_stack_plan(self.case)


if __name__ == "__main__":
    unittest.main()
