"""Tests for importing NISAR RSLC HDF5 products through GAMMA."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.adapters.nisar import (
    NisarRslcAdapter,
)
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import GammaRadarSlcCase
from tests.test_nisar_hdf5 import write_rslc


class NisarGammaSlcAdapterTests(unittest.TestCase):
    """Require exact GAMMA argv, channel validation, and complete output groups."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "RAW"
        self.raw.mkdir()
        self.slc = self.root / "SLC"
        self.case = GammaRadarSlcCase(
            name="nisar_la",
            sensor="nisar",
            raw_dir=self.raw,
            slc_dir=self.slc,
            images_file=self.root / "images_nisar_la",
            polarizations=("HH", "HV"),
            range_looks=10,
            azimuth_looks=10,
            commands=MappingProxyType({}),
            workflow_task="PRE_GAMMA_SAR_SLC",
            adapter_options=MappingProxyType(
                {
                    "band": "L",
                    "frequency": "A",
                    "radiometric_calibration": "1",
                    "noise_subtraction": "0",
                }
            ),
        )
        self.product = self.raw / "NISAR_TEST.h5"
        write_rslc(self.product, bands={"L": {"A": ("HH", "HV"), "B": ("HH",)}})
        self.adapter = NisarRslcAdapter()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_discovers_one_acquisition_per_hdf5_and_selected_channels(self) -> None:
        second = self.raw / "NISAR_SECOND.h5"
        write_rslc(second, bands={"L": {"A": ("HH", "HV")}})
        with __import__("h5py").File(second, "a") as product:
            product["science/LSAR/identification/zeroDopplerStartTime"][()] = b"2026-01-06T10:20:30Z"
            product["science/LSAR/identification/zeroDopplerEndTime"][()] = b"2026-01-06T10:21:02Z"

        products = self.adapter.discover(self.case)

        self.assertEqual(tuple(item.date for item in products), ("20260105", "20260106"))
        self.assertEqual(products[0].source_files, (self.product.resolve(),))
        self.assertEqual(products[0].polarizations, ("HH", "HV"))

    def test_rejects_mixed_track_frame_or_direction_in_one_stack(self) -> None:
        second = self.raw / "NISAR_SECOND.h5"
        write_rslc(second, bands={"L": {"A": ("HH", "HV")}})
        with __import__("h5py").File(second, "a") as product:
            identification = product["science/LSAR/identification"]
            identification["zeroDopplerStartTime"][()] = b"2026-01-06T10:20:30Z"
            identification["zeroDopplerEndTime"][()] = b"2026-01-06T10:21:02Z"
            identification["frameNumber"][()] = 23

        with self.assertRaisesRegex(ValueError, "one track/frame/direction"):
            self.adapter.discover(self.case)

    def test_builds_exact_import_and_preview_commands_with_fcomplex_outputs(self) -> None:
        product = self.adapter.discover(self.case)[0]
        staging = self.root / "stage"
        staging.mkdir()
        prepared = self.adapter.prepare(product, staging)

        plan = self.adapter.build_scene_plan(self.case, prepared)

        root = staging / "products/HH/20260105/20260105"
        self.assertEqual(
            plan.commands[0].argv,
            (
                "par_NISAR_RSLC",
                str(self.product.resolve()),
                str(root),
                "1",
                "0",
                "L",
                "A",
                "HH",
                "0",
            ),
        )
        self.assertEqual(plan.commands[1].stage, "preview_hh")
        self.assertEqual(plan.commands[1].parameter_file, Path(str(root) + ".slc.par"))
        self.assertEqual(
            tuple(output.staged_relative.name for output in plan.outputs[:3]),
            ("20260105.slc", "20260105.slc.par", "20260105.slc.bmp"),
        )
        self.assertEqual(plan.state, "run")

    def test_finalize_requires_fcomplex_and_real_positive_center_frequency(self) -> None:
        product = self.adapter.discover(self.case)[0]
        staging = self.root / "stage"
        staging.mkdir()
        prepared = self.adapter.prepare(product, staging)
        plan = self.adapter.build_scene_plan(self.case, prepared)
        for output in plan.outputs:
            path = staging / output.staged_relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name.endswith(".slc.par"):
                path.write_text(
                    "range_samples: 4\nimage_format: FCOMPLEX\ncenter_frequency: 1.2575e9 Hz\n",
                    encoding="ascii",
                )
            else:
                path.write_bytes(b"data")
        self.adapter.finalize_scene(prepared, plan)

        parameter = staging / plan.outputs[1].staged_relative
        parameter.write_text(parameter.read_text().replace("FCOMPLEX", "SCOMPLEX"))
        with self.assertRaisesRegex(ValueError, "FCOMPLEX"):
            self.adapter.finalize_scene(prepared, plan)
        parameter.write_text("range_samples: 4\nimage_format: FCOMPLEX\ncenter_frequency: 0 Hz\n")
        with self.assertRaisesRegex(ValueError, "center_frequency"):
            self.adapter.finalize_scene(prepared, plan)

    def test_complete_outputs_skip_and_partial_outputs_stop(self) -> None:
        product = self.adapter.discover(self.case)[0]
        staging = self.root / "stage"
        staging.mkdir()
        prepared = self.adapter.prepare(product, staging)
        first = self.adapter.build_scene_plan(self.case, prepared)
        for output in first.outputs:
            output.destination.parent.mkdir(parents=True, exist_ok=True)
            output.destination.write_bytes(b"complete")
        self.assertEqual(self.adapter.build_scene_plan(self.case, prepared).state, "skip")
        first.outputs[0].destination.unlink()
        with self.assertRaisesRegex(ValueError, "incomplete output group"):
            self.adapter.build_scene_plan(self.case, prepared)

    def test_nisar_adapter_rejects_missing_selected_dataset(self) -> None:
        missing = GammaRadarSlcCase(
            **{**self.case.__dict__, "polarizations": ("VV",)}
        )
        with self.assertRaisesRegex(ValueError, "L/A/VV"):
            self.adapter.discover(missing)

    def test_gamma_template_has_separate_la_and_optional_lb_cases(self) -> None:
        template = (
            Path(__file__).resolve().parents[1]
            / "config_examples/preprocessing/gamma_slc_grid.cfg"
        )
        contents = template.read_text(encoding="utf-8")
        for marker in (
            "[case:nisar_la]",
            "[case:nisar_lb]",
            "adapter.band = L",
            "adapter.frequency = A",
            "adapter.frequency = B",
            "adapter.radiometric_calibration = beta0",
            "adapter.noise_subtraction = false",
            "par_NISAR_RSLC",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, contents)


if __name__ == "__main__":
    unittest.main()
