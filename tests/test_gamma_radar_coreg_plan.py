"""Tests for deterministic GAMMA radar-grid coregistration plans."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_config import (
    GammaRadarCoregCase,
    GammaRadarQualityThresholds,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_plan import (
    build_gamma_radar_coreg_plan,
)


PARAMETERS = """range_samples: 100
azimuth_lines: 80
range_pixel_spacing: 2.0 m
azimuth_pixel_spacing: 3.0 m
center_frequency: 1.2575e9 Hz
"""


class GammaRadarCoregPlanTests(unittest.TestCase):
    """Plan every date/polarization while preserving immutable inputs."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.slc = self.root / "SLC"
        self.images = self.root / "images"
        self.images.write_text("20240312\n20240229\n", encoding="ascii")
        for date in ("20240229", "20240312"):
            for polarization in ("HH", "HV"):
                scene = self.slc / f"{polarization}_SLC" / date
                scene.mkdir(parents=True)
                (scene / f"{date}.slc").write_bytes(f"{date}-{polarization}".encode())
                (scene / f"{date}.slc.par").write_text(PARAMETERS, encoding="ascii")
        self.geometry = self.root / "GEOMETRY"
        self.geometry.mkdir()
        self.height = self.geometry / "20240229.HH.wgs84.hgt"
        self.height.write_bytes(b"height")
        self.geometry_manifest = self.geometry / "reference_geometry.json"
        self.geometry_manifest.write_text(
            json.dumps(
                {
                    "reference_date": "20240229",
                    "primary_polarization": "HH",
                    "outputs": [self.height.name],
                }
            ),
            encoding="utf-8",
        )
        self.output = self.root / "RSLC"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(self, **changes: object) -> GammaRadarCoregCase:
        values: dict[str, object] = {
            "name": "main",
            "sensor": "nisar",
            "frequency": "A",
            "reference_date": "20240229",
            "primary_polarization": "HH",
            "selected_polarizations": ("HH", "HV"),
            "slc_dir": self.slc,
            "images_file": self.images,
            "reference_geometry": self.geometry_manifest,
            "output_dir": self.output,
            "range_looks": 5,
            "azimuth_looks": 3,
            "refinement": "standard",
            "sar_mode": 1,
            "offset_polynomial_parameters": 6,
            "offset_range_estimates": 128,
            "offset_azimuth_estimates": 128,
            "offset_range_window": 256,
            "offset_azimuth_window": 256,
            "offset_oversampling": 1,
            "offset_correlation_threshold": 0.35,
            "refine_range_step": 128,
            "refine_azimuth_step": 128,
            "refine_range_window": 256,
            "refine_azimuth_window": 256,
            "refine_oversampling": 1,
            "refine_correlation_threshold": 0.35,
            "quality_thresholds": GammaRadarQualityThresholds(0.15, 0.30, 0.20, 0.50),
            "track": "87",
            "frame": "22",
            "orbit_direction": "DESCENDING",
        }
        values.update(changes)
        return GammaRadarCoregCase(**values)  # type: ignore[arg-type]

    def test_reference_first_complete_grid_and_exact_coreg_argv(self) -> None:
        plan = build_gamma_radar_coreg_plan(self._case())
        self.assertEqual(plan.dates, ("20240229", "20240312"))
        self.assertEqual(len(plan.scenes), 4)
        self.assertEqual(
            [(scene.asset.date, scene.asset.polarization) for scene in plan.scenes],
            [("20240229", "HH"), ("20240229", "HV"), ("20240312", "HH"), ("20240312", "HV")],
        )
        self.assertTrue(plan.scenes[0].copy_reference)
        self.assertTrue(plan.scenes[1].copy_reference)
        command = plan.scenes[2].commands[0].argv
        self.assertEqual(command[0], "SLC_coreg.py")
        self.assertEqual(command[1], str(self.slc / "HH_SLC/20240312/20240312.slc"))
        self.assertIn(str(self.height), command)
        self.assertEqual(
            command[-16:],
            (
                "--sar_mode", "1",
                "--npoly", "6",
                "--nr", "128",
                "--naz", "128",
                "--rwin", "256",
                "--azwin", "256",
                "--n_ovr", "1",
                "--thres", "0.35",
            ),
        )
        self.assertEqual(plan.center_frequency_hz, 1.2575e9)
        self.assertFalse(self.output.exists())

    def test_missing_polarization_date_blocks_incomplete_grid(self) -> None:
        (self.slc / "HV_SLC/20240312/20240312.slc").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "complete date/polarization grid"):
            build_gamma_radar_coreg_plan(self._case())

    def test_geometry_reference_and_center_frequency_must_be_consistent(self) -> None:
        manifest = json.loads(self.geometry_manifest.read_text())
        manifest["reference_date"] = "20240312"
        self.geometry_manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "reference date"):
            build_gamma_radar_coreg_plan(self._case())
        manifest["reference_date"] = "20240229"
        self.geometry_manifest.write_text(json.dumps(manifest))
        parameter = self.slc / "HV_SLC/20240312/20240312.slc.par"
        parameter.write_text(PARAMETERS.replace("1.2575e9", "1.2e9"), encoding="ascii")
        with self.assertRaisesRegex(ValueError, "center frequency"):
            build_gamma_radar_coreg_plan(self._case())

    def test_coreg_plan_sets_sensor_sar_mode(self) -> None:
        expected = {"lt1": 0, "nisar": 1, "alos2": 1, "saocom": 1, "rsat2": 1, "cosmo": 1}
        for sensor, mode in expected.items():
            with self.subTest(sensor=sensor):
                plan = build_gamma_radar_coreg_plan(self._case(sensor=sensor, sar_mode=mode))
                command = next(scene for scene in plan.scenes if not scene.copy_reference).commands[0].argv
                self.assertEqual(command[command.index("--sar_mode") + 1], str(mode))

    def test_secondary_polarization_reuses_arbitrary_primary_geometry(self) -> None:
        manifest = json.loads(self.geometry_manifest.read_text())
        manifest["primary_polarization"] = "HV"
        self.geometry_manifest.write_text(json.dumps(manifest))
        plan = build_gamma_radar_coreg_plan(
            self._case(
                primary_polarization="HV",
                selected_polarizations=("HH", "HV"),
            )
        )
        self.assertEqual(
            [(scene.asset.date, scene.asset.polarization) for scene in plan.scenes],
            [("20240229", "HV"), ("20240229", "HH"), ("20240312", "HV"), ("20240312", "HH")],
        )
        secondary = plan.scenes[-1]
        self.assertEqual(secondary.commands[0].argv[0], "SLC_interp_lt")
        self.assertIn("HV_RSLC/20240312/20240312.lt_fine", secondary.commands[0].argv[4])
        self.assertIn("HV_RSLC/20240312/20240312.off", secondary.commands[0].argv[7])

    def test_secondary_polarization_requires_identical_scene_grid(self) -> None:
        parameter = self.slc / "HV_SLC/20240312/20240312.slc.par"
        parameter.write_text(
            PARAMETERS.replace("range_samples: 100", "range_samples: 99"),
            encoding="ascii",
        )
        with self.assertRaisesRegex(ValueError, "identical scene grid"):
            build_gamma_radar_coreg_plan(self._case())

        parameter.write_text(
            PARAMETERS + "orbit: 2\n",
            encoding="ascii",
        )
        primary = self.slc / "HH_SLC/20240312/20240312.slc.par"
        primary.write_text(PARAMETERS + "orbit: 1\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "orbit"):
            build_gamma_radar_coreg_plan(self._case())

    def test_offset_field_refinement_is_explicit_and_after_standard_coreg(self) -> None:
        standard = build_gamma_radar_coreg_plan(self._case())
        primary = next(
            scene
            for scene in standard.scenes
            if scene.asset.date == "20240312" and scene.asset.polarization == "HH"
        )
        self.assertEqual([item.argv[0] for item in primary.commands], ["SLC_coreg.py"])

        refined = build_gamma_radar_coreg_plan(self._case(refinement="offset_field"))
        primary = next(
            scene
            for scene in refined.scenes
            if scene.asset.date == "20240312" and scene.asset.polarization == "HH"
        )
        self.assertEqual(
            [item.argv[0] for item in primary.commands],
            ["SLC_coreg.py", "SLC_coreg_refine.py"],
        )
        self.assertEqual(
            primary.commands[1].argv[-15:],
            (
                "--rstep", "128",
                "--azstep", "128",
                "--rwin", "256",
                "--azwin", "256",
                "--n_ovr", "1",
                "--cc_thres", "0.35",
                "--quality", primary.commands[1].argv[-2],
                "--offset_img",
            ),
        )


if __name__ == "__main__":
    unittest.main()
