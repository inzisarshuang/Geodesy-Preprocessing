"""Tests for safe Sentinel-1 DEM-geocoding planning."""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path


class GeoRefPlanTests(unittest.TestCase):
    """Reject ambiguous inputs and preserve existing output directories."""

    def _inputs(self, root: Path) -> tuple[Path, Path]:
        dem = root / "DEM"
        dem.mkdir()
        (dem / "srtm.dem").write_bytes(b"dem")
        (dem / "srtm.dem.par").write_text("width: 2\nnlines: 2\n", encoding="ascii")

        slc = root / "SLC"
        scene = slc / "20240905"
        scene.mkdir(parents=True)
        for suffix in ("vv.SLC_tab", "vv.iw1.slc", "vv.iw1.slc.par"):
            (scene / f"20240905.{suffix}").write_bytes(b"slc")
        return dem, slc

    def _module(self):
        try:
            return importlib.import_module(
                "geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_plan"
            )
        except ModuleNotFoundError:
            self.fail("geo_ref_plan module has not been implemented")

    def test_builds_resolved_immutable_plan_for_new_destination(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dem, slc = self._inputs(root)

            plan = module.build_geo_ref_plan(
                "20240905", dem, slc, root / "DEM_geocode", 7, 2
            )

            self.assertEqual(plan.reference, "20240905")
            self.assertEqual(plan.dem_file, dem / "srtm.dem")
            self.assertEqual(plan.dem_par, dem / "srtm.dem.par")
            self.assertEqual(
                tuple(path.name for path in plan.slc_sources),
                (
                    "20240905.vv.SLC_tab",
                    "20240905.vv.iw1.slc",
                    "20240905.vv.iw1.slc.par",
                ),
            )
            self.assertEqual((plan.range_looks, plan.azimuth_looks), (7, 2))
            self.assertIsNone(plan.latitude_oversampling)
            self.assertIsNone(plan.longitude_oversampling)
            self.assertEqual((plan.reference_flag, plan.incidence_flag), (1, 0))
            self.assertFalse(plan.skip)
            with self.assertRaises(Exception):
                plan.reference = "20240906"

    def test_complete_destination_skips_but_incomplete_destination_is_rejected(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dem, slc = self._inputs(root)
            output = root / "DEM_geocode"
            output.mkdir()
            required = (
                "20240905.lt_fine",
                "20240905.utm.dem",
                "20240905.utm.dem.par",
                "20240905.mli.utm",
                "20240905.hgt_sim",
            )
            for name in required:
                (output / name).write_bytes(b"complete")

            plan = module.build_geo_ref_plan("20240905", dem, slc, output, 7, 2)

            self.assertTrue(plan.skip)
            (output / required[-1]).unlink()
            with self.assertRaisesRegex(FileExistsError, "incomplete"):
                module.build_geo_ref_plan("20240905", dem, slc, output, 7, 2)

    def test_rejects_invalid_reference_ambiguous_dem_and_partial_oversampling(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dem, slc = self._inputs(root)
            with self.assertRaisesRegex(ValueError, "reference"):
                module.build_geo_ref_plan("../bad", dem, slc, root / "out", 7, 2)

            with self.assertRaisesRegex(ValueError, "provided together"):
                module.build_geo_ref_plan(
                    "20240905",
                    dem,
                    slc,
                    root / "out",
                    7,
                    2,
                    latitude_oversampling=1.2,
                )

            (dem / "second.dem").write_bytes(b"other")
            with self.assertRaisesRegex(ValueError, "exactly one DEM data"):
                module.build_geo_ref_plan("20240905", dem, slc, root / "out", 7, 2)


if __name__ == "__main__":
    unittest.main()
