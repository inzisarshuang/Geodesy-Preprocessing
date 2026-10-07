"""Tests for staged Sentinel-1 reference DEM geocoding."""

from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_plan import build_geo_ref_plan


class GeoRefRunnerTests(unittest.TestCase):
    """Exercise auto-oversampling, GAMMA sequencing, and atomic publication."""

    def _module(self):
        try:
            return importlib.import_module(
                "geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_runner"
            )
        except ModuleNotFoundError:
            self.fail("geo_ref_runner module has not been implemented")

    def _plan(self, root: Path, *, explicit: bool = False):
        dem = root / "DEM"
        dem.mkdir()
        (dem / "srtm.dem").write_bytes(b"dem")
        (dem / "srtm.dem.par").write_text(
            "corner_lat: 40\npost_lat: -0.00012\npost_lon: 0.00018\n",
            encoding="ascii",
        )
        scene = root / "SLC" / "20240905"
        scene.mkdir(parents=True)
        names = (
            "20240905.vv.iw1.slc",
            "20240905.vv.iw1.slc.par",
            "20240905.vv.iw1.slc.tops_par",
        )
        for name in names:
            (scene / name).write_bytes(b"slc")
        (scene / "20240905.vv.SLC_tab").write_text(
            " ".join(names) + "\n", encoding="ascii"
        )
        options = (
            {"latitude_oversampling": 1.25, "longitude_oversampling": 1.5}
            if explicit
            else {}
        )
        return build_geo_ref_plan(
            "20240905", dem, root / "SLC", root / "DEM_geocode", 7, 2, **options
        )

    def test_estimates_geographic_dem_oversampling_from_gamma_parameters(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mli = root / "reference.mli.par"
            mli.write_text(
                "range_pixel_spacing: 2\n"
                "azimuth_pixel_spacing: 4\n"
                "incidence_angle: 30\n"
                "center_latitude: 60\n",
                encoding="ascii",
            )
            dem = root / "srtm.dem.par"
            dem.write_text(
                "corner_lat: 40\npost_lat: -0.00012\npost_lon: 0.00018\n",
                encoding="ascii",
            )

            latitude, longitude = module.estimate_oversampling(mli, dem)

            self.assertEqual((latitude, longitude), (3.351, 2.508))

    def test_estimation_preserves_legacy_bc_and_awk_rounding(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mli = root / "reference.mli.par"
            mli.write_text(
                "center_latitude: 40.3389064\n"
                "range_pixel_spacing: 16.3069340\n"
                "azimuth_pixel_spacing: 27.9100200\n"
                "incidence_angle: 36.9790\n",
                encoding="ascii",
            )
            dem = root / "srtm.dem.par"
            dem.write_text(
                "corner_lat: 41.0236111\n"
                "post_lat: -2.777777778e-04\n"
                "post_lon: 2.777777778e-04\n",
                encoding="ascii",
            )

            self.assertEqual(module.estimate_oversampling(mli, dem), (1.112, 0.871))

    def test_runner_executes_literal_stage_order_and_creates_required_products(self) -> None:
        module = self._module()

        class SimulatedRunner(module.GeoRefRunner):
            def _execute(
                self, stage, command, cwd, *, stdout_path=None, allow_failure=False
            ):
                reference = "20240905"
                if stage == "mosaic":
                    (cwd / f"{reference}.slc").write_bytes(b"slc")
                    (cwd / f"{reference}.slc.par").write_text(
                        "range_samples: 10\n", encoding="ascii"
                    )
                elif stage == "multi_look":
                    (cwd / f"{reference}.mli").write_bytes(b"mli")
                    (cwd / f"{reference}.mli.par").write_text(
                        "range_samples: 10\n"
                        "azimuth_lines: 20\n"
                        "range_pixel_spacing: 2\n"
                        "azimuth_pixel_spacing: 4\n"
                        "incidence_angle: 30\n"
                        "center_latitude: 60\n",
                        encoding="ascii",
                    )
                elif stage == "gc_map2":
                    for suffix in ("utm.dem", "lt", "ls_map", "inc"):
                        (cwd / f"{reference}.{suffix}").write_bytes(b"product")
                    (cwd / f"{reference}.utm.dem.par").write_text(
                        "width: 30\nnlines: 40\n", encoding="ascii"
                    )
                elif stage == "gc_map_fine":
                    (cwd / f"{reference}.lt_fine").write_bytes(b"fine")
                elif stage == "geocode_back":
                    (cwd / f"{reference}.mli.utm").write_bytes(b"mli")
                elif stage == "geocode":
                    (cwd / f"{reference}.hgt_sim").write_bytes(b"height")
                return module.GeoRefCommandResult(stage, tuple(command), 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)
            staging = root / "stage"
            staging.mkdir()

            results = SimulatedRunner().run_stages(plan, staging)

            self.assertEqual(
                tuple(result.stage for result in results),
                (
                    "mosaic",
                    "multi_look",
                    "preview",
                    "gc_map2",
                    "pixel_area",
                    "create_diff_par",
                    "init_offsetm",
                    "offset_pwrm",
                    "offset_fitm",
                    "gc_map_fine",
                    "geocode_back",
                    "preview_utm_bmp",
                    "preview_utm_tif",
                    "geocode",
                    "preview_height",
                ),
            )
            self.assertTrue((staging / "lat_lon_ovr").is_file())
            self.assertEqual(
                (staging / "lat_lon_ovr").read_text(encoding="ascii"),
                "lat_ovr: 3.351\nlon_ovr: 2.508\n",
            )
            self.assertTrue(all((staging / path.name).is_file() for path in plan.expected_outputs))

    def test_runner_isolates_gamma_writes_to_source_parameters(self) -> None:
        module = self._module()

        class StopAfterMosaic(RuntimeError):
            pass

        class MetadataWritingRunner(module.GeoRefRunner):
            def _execute(
                self, stage, command, cwd, *, stdout_path=None, allow_failure=False
            ):
                mutable = cwd / "20240905.vv.iw1.slc.tops_par"
                self.parameter_is_copy = not mutable.is_symlink()
                mutable.write_bytes(b"gamma-updated")
                raise StopAfterMosaic

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)
            source = (
                plan.slc_dir
                / "20240905"
                / "20240905.vv.iw1.slc.tops_par"
            )
            original = source.read_bytes()
            staging = root / "stage"
            staging.mkdir()
            runner = MetadataWritingRunner()

            with self.assertRaises(StopAfterMosaic):
                runner.run_stages(plan, staging)

            self.assertTrue(runner.parameter_is_copy)
            self.assertEqual(source.read_bytes(), original)

    def test_runner_rewrites_absolute_tab_sources_to_the_isolated_view(self) -> None:
        module = self._module()

        class StopAfterInspection(RuntimeError):
            pass

        class TabInspectingRunner(module.GeoRefRunner):
            def _execute(
                self, stage, command, cwd, *, stdout_path=None, allow_failure=False
            ):
                self.fields = (
                    cwd / "20240905.vv.SLC_tab"
                ).read_text(encoding="ascii").split()
                raise StopAfterInspection

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)
            scene = plan.slc_dir / "20240905"
            names = (
                "20240905.vv.iw1.slc",
                "20240905.vv.iw1.slc.par",
                "20240905.vv.iw1.slc.tops_par",
            )
            (scene / "20240905.vv.SLC_tab").write_text(
                " ".join(str(scene / name) for name in names) + "\n",
                encoding="ascii",
            )
            staging = root / "stage"
            staging.mkdir()
            runner = TabInspectingRunner()

            with self.assertRaises(StopAfterInspection):
                runner.run_stages(plan, staging)

            self.assertEqual(runner.fields, list(names))

    def test_batch_wrapper_atomically_publishes_and_emits_success(self) -> None:
        module = self._module()

        class ProductRunner:
            def run_stages(self, plan, staging, *, stage_sink=None):
                if stage_sink is not None:
                    stage_sink("geocode")
                for expected in plan.expected_outputs:
                    (staging / expected.name).write_bytes(b"complete")
                return ()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root, explicit=True)
            events: list[str] = []

            result = module.run_geo_ref(
                plan, runner=ProductRunner(), progress_sink=events.append
            )

            self.assertEqual(result.status, "success")
            self.assertTrue(plan.output_dir.is_dir())
            self.assertTrue(all(path.is_file() for path in plan.expected_outputs))
            self.assertFalse(any(root.glob(".DEM_geocode.*.stage")))
            payloads = [json.loads(line.removeprefix("@@GEODESY_PROGRESS ")) for line in events]
            self.assertEqual(payloads[-1]["status"], "success")
            self.assertEqual(payloads[-1]["item"], "20240905")

    def test_concurrent_destination_is_never_replaced(self) -> None:
        module = self._module()

        class RacingRunner:
            def run_stages(self, plan, staging, *, stage_sink=None):
                plan.output_dir.mkdir()
                for expected in plan.expected_outputs:
                    (staging / expected.name).write_bytes(b"complete")
                return ()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root, explicit=True)

            with self.assertRaisesRegex(FileExistsError, "appeared during execution"):
                module.run_geo_ref(plan, runner=RacingRunner(), progress_sink=lambda line: None)

            self.assertTrue(plan.output_dir.is_dir())
            self.assertEqual(tuple(plan.output_dir.iterdir()), ())
            self.assertFalse(any(root.glob(".DEM_geocode.*.stage")))


if __name__ == "__main__":
    unittest.main()
