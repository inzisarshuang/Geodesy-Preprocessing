"""Behavior tests for CSG and CSKS HDF5 adapters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from fixtures.other_sar import write_cosmo_product
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.adapters.cosmo import CsgAdapter, CsksAdapter
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import OtherSarSlcCase


class CosmoAdapterTests(unittest.TestCase):
    """Pin separate date rules, commands, layouts, and converter checks."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "RAW"
        self.raw.mkdir()
        self.slc = self.root / "SLC"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(
        self,
        sensor: str,
        *,
        polarizations: tuple[str, ...] = ("HH",),
        command: str | None = None,
    ) -> OtherSarSlcCase:
        commands = {} if command is None else {"import_command": command}
        return OtherSarSlcCase(
            name="main",
            sensor=sensor,
            raw_dir=self.raw,
            slc_dir=self.slc,
            images_file=self.root / f"images_{sensor}",
            polarizations=polarizations,
            range_looks=10,
            azimuth_looks=10,
            commands=MappingProxyType(commands),
            workflow_task="PRE_OTHER_SAR_SLC",
        )

    def test_csg_builds_exact_command_normalizes_output_and_uses_legacy_layout(self) -> None:
        product_file = write_cosmo_product(self.raw, sensor="csg", date="20260922")
        adapter = CsgAdapter()
        case = self._case("csg")
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        prepared = adapter.prepare(product, stage)
        plan = adapter.build_scene_plan(case, prepared)

        self.assertEqual(
            plan.commands[0].argv,
            ("par_CSG_SLC", str(product_file), "20260922"),
        )
        self.assertEqual(
            tuple(output.destination for output in plan.outputs),
            (
                self.slc / "HH_SLC/CSG/20260922.slc",
                self.slc / "HH_SLC/CSG/20260922.slc.par",
            ),
        )
        (stage / "20260922_HH_S1.slc").write_bytes(b"slc")
        (stage / "20260922_HH_S1.slc.par").write_bytes(b"par")
        adapter.finalize_scene(prepared, plan)
        self.assertEqual((stage / "products/HH/20260922.slc").read_bytes(), b"slc")
        self.assertEqual((stage / "products/HH/20260922.slc.par").read_bytes(), b"par")

    def test_csks_uses_its_own_filename_marker_command_and_layout(self) -> None:
        product_file = write_cosmo_product(self.raw, sensor="csks", date="20260923")
        adapter = CsksAdapter()
        case = self._case("csks")
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        prepared = adapter.prepare(product, stage)
        plan = adapter.build_scene_plan(case, prepared)
        self.assertEqual(
            plan.commands[0].argv,
            ("par_CS_SLC", str(product_file), "20260923"),
        )
        self.assertEqual(
            plan.outputs[0].destination,
            self.slc / "HH_SLC/CSKS/20260923.slc",
        )

    def test_zero_two_products_and_wrong_filename_marker_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one HDF5"):
            CsgAdapter().discover(self._case("csg"))
        write_cosmo_product(self.raw, sensor="csg", date="20260922")
        second = self.raw / "20260922/PRODUCT/second.h5"
        second.write_bytes(b"second")
        with self.assertRaisesRegex(ValueError, "exactly one HDF5"):
            CsgAdapter().discover(self._case("csg"))

        second.unlink()
        with self.assertRaisesRegex(ValueError, "CSKS filename pattern"):
            CsksAdapter().discover(self._case("csks"))

    def test_directory_date_mismatch_duplicate_dates_and_non_hh_are_rejected(self) -> None:
        write_cosmo_product(
            self.raw, sensor="csg", date="20260922", scene_name="20260921"
        )
        with self.assertRaisesRegex(ValueError, "does not match directory date"):
            CsgAdapter().discover(self._case("csg"))

        for child in self.raw.iterdir():
            if child.is_dir():
                import shutil

                shutil.rmtree(child)
        write_cosmo_product(self.raw, sensor="csg", date="20260922", scene_name="scene-a")
        write_cosmo_product(self.raw, sensor="csg", date="20260922", scene_name="scene-b")
        with self.assertRaisesRegex(ValueError, "duplicate acquisition date"):
            CsgAdapter().discover(self._case("csg"))

        with self.assertRaisesRegex(ValueError, "only HH"):
            CsgAdapter().discover(self._case("csg", polarizations=("HH", "HV")))

    def test_explicit_converter_must_exist_and_be_executable(self) -> None:
        write_cosmo_product(self.raw, sensor="csg", date="20260922")
        missing = self.root / "missing-converter"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            CsgAdapter().discover(self._case("csg", command=str(missing)))

        converter = self.root / "converter"
        converter.write_text("#!/bin/sh\nexit 0\n")
        converter.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "not executable"):
            CsgAdapter().discover(self._case("csg", command=str(converter)))
        converter.chmod(0o700)
        self.assertEqual(len(CsgAdapter().discover(self._case("csg", command=str(converter)))), 1)

    def test_finalize_requires_one_generated_pair(self) -> None:
        write_cosmo_product(self.raw, sensor="csg", date="20260922")
        adapter = CsgAdapter()
        case = self._case("csg")
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        prepared = adapter.prepare(product, stage)
        plan = adapter.build_scene_plan(case, prepared)
        with self.assertRaisesRegex(ValueError, "exactly one generated SLC"):
            adapter.finalize_scene(prepared, plan)
        (stage / "20260922_A.slc").write_bytes(b"a")
        (stage / "20260922_B.slc").write_bytes(b"b")
        (stage / "20260922_A.slc.par").write_bytes(b"p")
        with self.assertRaisesRegex(ValueError, "exactly one generated SLC"):
            adapter.finalize_scene(prepared, plan)


if __name__ == "__main__":
    unittest.main()
