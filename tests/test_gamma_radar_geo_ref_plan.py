"""Tests for read-only GAMMA radar-grid reference-geometry planning."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_config import (
    GammaRadarGeoRefCase,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_plan import (
    build_gamma_radar_geo_ref_plan,
)


PARAMETERS = """range_samples: 100
azimuth_lines: 80
range_pixel_spacing: 2.0 m
azimuth_pixel_spacing: 3.0 m
"""


class GammaRadarGeoRefPlanTests(unittest.TestCase):
    """Reject incomplete inputs and classify complete destinations without writes."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.slc = self.root / "SLC"
        self.images = self.root / "images"
        self.images.write_text("20240229\n20240312\n", encoding="ascii")
        self.dem = self.root / "DEM/srtm.dem"
        self.dem.parent.mkdir()
        self.dem.write_bytes(b"dem")
        self.dem_par = self.root / "DEM/srtm.dem.par"
        self.dem_par.write_text("width: 10\nnlines: 20\n", encoding="ascii")
        self.output = self.root / "GEOMETRY"
        self._scene("20240229", "HH")
        self._scene("20240312", "HH")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _scene(self, date: str, polarization: str) -> None:
        scene = self.slc / f"{polarization}_SLC" / date
        scene.mkdir(parents=True, exist_ok=True)
        (scene / f"{date}.slc").write_bytes(b"slc")
        (scene / f"{date}.slc.par").write_text(PARAMETERS, encoding="ascii")

    def _case(self, **changes: object) -> GammaRadarGeoRefCase:
        values: dict[str, object] = {
            "name": "main",
            "reference_date": "20240229",
            "primary_polarization": "HH",
            "slc_dir": self.slc,
            "images_file": self.images,
            "dem": self.dem,
            "dem_par": self.dem_par,
            "output_dir": self.output,
            "range_looks": 5,
            "azimuth_looks": 3,
            "latitude_oversampling": None,
            "longitude_oversampling": None,
            "post_spacing": None,
            "correlation_threshold": 0.2,
            "polynomial_parameters": 1,
            "range_offset_estimates": None,
            "azimuth_offset_estimates": None,
            "range_window": 128,
            "azimuth_window": 128,
            "area_factor": 1.0,
        }
        values.update(changes)
        return GammaRadarGeoRefCase(**values)  # type: ignore[arg-type]

    def test_builds_expected_reference_geometry_outputs_without_writing(self) -> None:
        plan = build_gamma_radar_geo_ref_plan(self._case())
        self.assertEqual(plan.reference_asset.date, "20240229")
        self.assertEqual(plan.reference_asset.polarization, "HH")
        self.assertEqual(plan.reference_asset.data_path.name, "20240229.slc")
        self.assertEqual(plan.reference_grid.range_samples, 100)
        self.assertEqual(plan.reference_mli, self.output / "20240229.HH.mli")
        self.assertEqual(plan.reference_mli_par, self.output / "20240229.HH.mli.par")
        self.assertEqual(plan.wgs84_radar_height, self.output / "20240229.HH.wgs84.hgt")
        self.assertEqual(plan.refined_lookup_table, self.output / "srtm.20240229.HH.lt_fine")
        self.assertEqual(plan.incidence_map, self.output / "srtm.20240229.HH.inc")
        self.assertEqual(plan.layover_shadow_map, self.output / "srtm.20240229.HH.ls_map")
        self.assertEqual(plan.geocoding_quality, self.output / "20240229.HH.geocoding_quality")
        self.assertEqual(plan.manifest, self.output / "reference_geometry.json")
        self.assertFalse(plan.skip)
        self.assertFalse(self.output.exists())

    def test_missing_reference_files_or_inventory_date_are_rejected(self) -> None:
        reference = self.slc / "HH_SLC/20240229/20240229.slc"
        reference.unlink()
        with self.assertRaises(FileNotFoundError):
            build_gamma_radar_geo_ref_plan(self._case())
        reference.write_bytes(b"slc")
        self.images.write_text("20240312\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "reference date.*images"):
            build_gamma_radar_geo_ref_plan(self._case())

    def test_polarization_and_dem_pair_must_match(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "primary polarization"):
            build_gamma_radar_geo_ref_plan(self._case(primary_polarization="VV"))
        other_par = self.dem.parent / "other.dem.par"
        other_par.write_text("width: 10\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "DEM data and parameter names"):
            build_gamma_radar_geo_ref_plan(self._case(dem_par=other_par))

    def test_complete_destination_skips(self) -> None:
        initial = build_gamma_radar_geo_ref_plan(self._case())
        self.output.mkdir()
        for path in initial.expected_outputs:
            path.write_bytes(b"complete")
        rebuilt = build_gamma_radar_geo_ref_plan(self._case())
        self.assertTrue(rebuilt.skip)

    def test_partial_destination_is_rejected(self) -> None:
        initial = build_gamma_radar_geo_ref_plan(self._case())
        self.output.mkdir()
        initial.reference_mli.write_bytes(b"partial")
        with self.assertRaisesRegex(FileExistsError, "incomplete reference-geometry"):
            build_gamma_radar_geo_ref_plan(self._case())


if __name__ == "__main__":
    unittest.main()
