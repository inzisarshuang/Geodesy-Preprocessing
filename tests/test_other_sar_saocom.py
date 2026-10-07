"""Behavior tests for supported SAOCOM product modes."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from fixtures.other_sar import write_saocom_product
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.adapters.saocom import (
    SaocomAdapter,
    saocom_polarizations_for_mode,
)
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import OtherSarSlcCase


def _snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


class SaocomAdapterTests(unittest.TestCase):
    """Pin ordered pairing, supported modes, commands, and source immutability."""

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
            sensor="saocom",
            raw_dir=self.raw,
            slc_dir=self.slc,
            images_file=self.root / "images_saocom",
            polarizations=polarizations,
            range_looks=10,
            azimuth_looks=20,
            commands=MappingProxyType({}),
            workflow_task="PRE_OTHER_SAR_SLC",
        )

    def test_single_mode_maps_one_pair_to_hh_without_changing_sources(self) -> None:
        scene = write_saocom_product(self.raw, date="20260922", pair_names=("Z",))
        before = _snapshot(self.raw)
        adapter = SaocomAdapter()
        case = self._case(saocom_polarizations_for_mode("aa"))
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        plan = adapter.build_scene_plan(case, adapter.prepare(product, stage))

        self.assertEqual(
            plan.commands[0].argv,
            (
                "par_SAOCOM_SLC",
                str(scene / "Data/Z.bin"),
                str(scene / "Data/Z.xml"),
                str(stage / "products/HH/20260922.slc.par"),
                str(stage / "products/HH/20260922.slc"),
                "-",
            ),
        )
        self.assertEqual(plan.outputs[0].destination, self.slc / "HH_SLC/20260922.slc")
        self.assertEqual(_snapshot(self.raw), before)

    def test_dual_mode_sorts_unordered_pairs_and_maps_to_hh_hv(self) -> None:
        scene = write_saocom_product(
            self.raw, date="20260923", pair_names=("B_SECOND", "A_FIRST")
        )
        adapter = SaocomAdapter()
        case = self._case(saocom_polarizations_for_mode("aa-ab"))
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        plan = adapter.build_scene_plan(case, adapter.prepare(product, stage))
        self.assertEqual(tuple(command.stage for command in plan.commands), ("import_hh", "import_hv"))
        self.assertEqual(plan.commands[0].argv[1:3], (str(scene / "Data/A_FIRST.bin"), str(scene / "Data/A_FIRST.xml")))
        self.assertEqual(plan.commands[1].argv[1:3], (str(scene / "Data/B_SECOND.bin"), str(scene / "Data/B_SECOND.xml")))
        self.assertEqual(
            tuple(output.destination for output in plan.outputs),
            (
                self.slc / "HH_SLC/20260923.slc",
                self.slc / "HH_SLC/20260923.slc.par",
                self.slc / "HV_SLC/20260923.slc",
                self.slc / "HV_SLC/20260923.slc.par",
            ),
        )

    def test_missing_ambiguous_pairs_and_duplicate_dates_are_rejected(self) -> None:
        scene = write_saocom_product(self.raw, date="20260922")
        (scene / "Data/A.bin").unlink()
        with self.assertRaisesRegex(ValueError, "matching raster"):
            SaocomAdapter().discover(self._case(("HH",)))

        (scene / "Data/A.bin").write_bytes(b"one")
        (scene / "Data/A.dat").write_bytes(b"two")
        with self.assertRaisesRegex(ValueError, "exactly one raster"):
            SaocomAdapter().discover(self._case(("HH",)))

        import shutil

        shutil.rmtree(scene)
        write_saocom_product(self.raw, date="20260922", scene_name="scene-a-20260922")
        write_saocom_product(self.raw, date="20260922", scene_name="scene-b-20260922")
        with self.assertRaisesRegex(ValueError, "duplicate acquisition date"):
            SaocomAdapter().discover(self._case(("HH",)))

    def test_incomplete_historical_modes_are_explicitly_rejected(self) -> None:
        self.assertEqual(saocom_polarizations_for_mode("aa"), ("HH",))
        self.assertEqual(saocom_polarizations_for_mode("aa-ab"), ("HH", "HV"))
        for mode in ("aa-bb", "full"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "historical branch was incomplete"):
                    saocom_polarizations_for_mode(mode)
        write_saocom_product(self.raw, date="20260922", pair_names=("A", "B", "C"))
        for polarizations in (("VH",), ("HH", "VV"), ("HH", "HV", "VH")):
            with self.subTest(polarizations=polarizations):
                with self.assertRaisesRegex(ValueError, "historical branch was incomplete"):
                    SaocomAdapter().discover(self._case(polarizations))


if __name__ == "__main__":
    unittest.main()
