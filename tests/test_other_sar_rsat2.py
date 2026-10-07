"""Behavior tests for the RADARSAT-2 vendor adapter."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from fixtures.other_sar import write_rsat2_product
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.adapters.rsat2 import Rsat2Adapter
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import OtherSarSlcCase


class Rsat2AdapterTests(unittest.TestCase):
    """Pin metadata dates, multi-pol commands, and scene-wide publication."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "RAW"
        self.raw.mkdir()
        self.slc = self.root / "SLC"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(self, polarizations: tuple[str, ...]) -> OtherSarSlcCase:
        return OtherSarSlcCase(
            name="main",
            sensor="rsat2",
            raw_dir=self.raw,
            slc_dir=self.slc,
            images_file=self.root / "images_rsat2",
            polarizations=polarizations,
            range_looks=20,
            azimuth_looks=10,
            commands=MappingProxyType({}),
            workflow_task="PRE_OTHER_SAR_SLC",
        )

    def test_quad_pol_builds_exact_commands_and_historical_folders(self) -> None:
        scene = write_rsat2_product(
            self.raw, date="20260922", polarizations=("HH", "HV", "VH", "VV")
        )
        adapter = Rsat2Adapter()
        case = self._case(("HH", "HV", "VH", "VV"))
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        prepared = adapter.prepare(product, stage)
        plan = adapter.build_scene_plan(case, prepared)

        expected = []
        for polarization in ("HH", "HV", "VH", "VV"):
            expected.append(
                (
                    "par_RSAT2_SLC",
                    str(scene / "product.xml"),
                    str(scene / "lutSigma.xml"),
                    str(scene / f"imagery_{polarization}.tif"),
                    polarization,
                    str(stage / f"products/{polarization}/20260922.slc.par"),
                    str(stage / f"products/{polarization}/20260922.slc"),
                )
            )
        self.assertEqual(tuple(command.argv for command in plan.commands), tuple(expected))
        self.assertEqual(
            tuple(output.destination for output in plan.outputs),
            tuple(
                path
                for polarization in ("HH", "HV", "VH", "VV")
                for path in (
                    self.slc / f"{polarization}_SLC/20260922.slc",
                    self.slc / f"{polarization}_SLC/20260922.slc.par",
                )
            ),
        )

    def test_missing_or_ambiguous_required_files_are_rejected(self) -> None:
        scene = write_rsat2_product(self.raw, date="20260922")
        for name, message in (
            ("product.xml", "product.xml"),
            ("lutSigma.xml", "lutSigma.xml"),
            ("imagery_HH.tif", "imagery_HH.tif"),
        ):
            with self.subTest(name=name):
                backup = (scene / name).read_bytes()
                (scene / name).unlink()
                with self.assertRaisesRegex(ValueError, message):
                    Rsat2Adapter().discover(self._case(("HH",)))
                (scene / name).write_bytes(backup)

        nested = scene / "duplicate"
        nested.mkdir()
        shutil.copy2(scene / "imagery_HH.tif", nested / "imagery_HH.tif")
        with self.assertRaisesRegex(ValueError, "exactly one imagery_HH.tif"):
            Rsat2Adapter().discover(self._case(("HH",)))

    def test_absent_requested_pol_bad_metadata_date_and_duplicate_date_fail(self) -> None:
        scene = write_rsat2_product(self.raw, date="20260922", polarizations=("HH",))
        with self.assertRaisesRegex(ValueError, "imagery_HV.tif"):
            Rsat2Adapter().discover(self._case(("HH", "HV")))

        (scene / "product.xml").write_text(
            "<product><rawDataStartTime>2026-99-22T10:11:12Z</rawDataStartTime></product>"
        )
        with self.assertRaisesRegex(ValueError, "valid acquisition date"):
            Rsat2Adapter().discover(self._case(("HH",)))

        shutil.rmtree(scene)
        write_rsat2_product(self.raw, date="20260922", scene_name="scene-a")
        write_rsat2_product(self.raw, date="20260922", scene_name="scene-b")
        with self.assertRaisesRegex(ValueError, "duplicate acquisition date"):
            Rsat2Adapter().discover(self._case(("HH",)))

    def test_partial_secondary_output_rejects_the_whole_scene(self) -> None:
        write_rsat2_product(self.raw, date="20260922", polarizations=("HH", "HV"))
        adapter = Rsat2Adapter()
        case = self._case(("HH", "HV"))
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        prepared = adapter.prepare(product, stage)
        hh = self.slc / "HH_SLC"
        hh.mkdir(parents=True)
        (hh / "20260922.slc").write_bytes(b"slc")
        (hh / "20260922.slc.par").write_bytes(b"par")

        with self.assertRaisesRegex(ValueError, "incomplete output group"):
            adapter.build_scene_plan(case, prepared)
        self.assertFalse((self.slc / "HV_SLC/20260922.slc").exists())


if __name__ == "__main__":
    unittest.main()
