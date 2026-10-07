"""Tests for safe Sentinel-1 stack-coregistration planning."""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path


class CoregPlanTests(unittest.TestCase):
    """Validate every scene and reject unsafe existing destinations."""

    def _module(self):
        try:
            return importlib.import_module(
                "geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_plan"
            )
        except ModuleNotFoundError:
            self.fail("coreg_plan module has not been implemented")

    def _inputs(self, root: Path) -> tuple[Path, Path, Path]:
        slc = root / "SLC"
        for date in ("20240905", "20240917"):
            scene = slc / date
            scene.mkdir(parents=True)
            names = (
                f"{date}.vv.iw1.slc",
                f"{date}.vv.iw1.slc.par",
                f"{date}.vv.iw1.slc.tops_par",
            )
            for name in names:
                (scene / name).write_bytes(date.encode("ascii"))
            (scene / f"{date}.vv.SLC_tab").write_text(
                " ".join(names) + "\n", encoding="utf-8"
            )
        images = root / "images_154"
        images.write_text("20240905\n20240917\n", encoding="ascii")
        dem_geo = root / "DEM_geocode"
        dem_geo.mkdir()
        (dem_geo / "20240905.hgt_sim").write_bytes(b"height")
        return slc, images, dem_geo

    def test_builds_ordered_immutable_plan_without_mutating_slc_root(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slc, images, dem_geo = self._inputs(root)
            before = tuple(sorted(path.relative_to(slc) for path in slc.rglob("*")))

            plan = module.build_coreg_plan(
                slc, dem_geo, root / "RSLC", "20240905", images, 7, 2
            )

            self.assertEqual([item.date for item in plan.items], ["20240905", "20240917"])
            self.assertEqual(plan.reference, "20240905")
            self.assertEqual(plan.height_file, dem_geo / "20240905.hgt_sim")
            self.assertEqual((plan.range_looks, plan.azimuth_looks), (7, 2))
            self.assertEqual(plan.bridge_mode, "none")
            self.assertEqual(plan.npoly, 4)
            self.assertFalse(plan.skip)
            self.assertEqual(
                tuple(path.name for path in plan.items[0].sources),
                (
                    "20240905.vv.iw1.slc",
                    "20240905.vv.iw1.slc.par",
                    "20240905.vv.iw1.slc.tops_par",
                ),
            )
            self.assertEqual(
                before, tuple(sorted(path.relative_to(slc) for path in slc.rglob("*")))
            )
            with self.assertRaises(Exception):
                plan.reference = "20240917"

    def test_complete_stack_skips_but_incomplete_destination_is_rejected(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slc, images, dem_geo = self._inputs(root)
            output = root / "RSLC"
            (output / "RSLC").mkdir(parents=True)
            (output / "coreg_quality").mkdir()
            for date in ("20240905", "20240917"):
                (output / "RSLC" / f"{date}.rslc").write_bytes(b"rslc")
                (output / "RSLC" / f"{date}.rslc.par").write_bytes(b"par")
            (output / "coreg_quality" / "1ID.coreg_quality").write_bytes(b"quality")

            plan = module.build_coreg_plan(
                slc, dem_geo, output, "20240905", images, 7, 2
            )

            self.assertTrue(plan.skip)
            (output / "RSLC" / "20240917.rslc").unlink()
            with self.assertRaisesRegex(FileExistsError, "incomplete"):
                module.build_coreg_plan(
                    slc, dem_geo, output, "20240905", images, 7, 2
                )

    def test_rejects_missing_reference_bad_tab_and_invalid_settings(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slc, images, dem_geo = self._inputs(root)
            with self.assertRaisesRegex(ValueError, "reference.*image list"):
                module.build_coreg_plan(
                    slc, dem_geo, root / "out", "20240929", images, 7, 2
                )

            tab = slc / "20240917" / "20240917.vv.SLC_tab"
            tab.write_text("only two-columns\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "three columns"):
                module.build_coreg_plan(
                    slc, dem_geo, root / "out", "20240905", images, 7, 2
                )

            tab.write_text(
                "20240917.vv.iw1.slc 20240917.vv.iw1.slc.par "
                "20240917.vv.iw1.slc.tops_par\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "bridge mode"):
                module.build_coreg_plan(
                    slc,
                    dem_geo,
                    root / "out",
                    "20240905",
                    images,
                    7,
                    2,
                    bridge_mode="other",
                )


if __name__ == "__main__":
    unittest.main()
