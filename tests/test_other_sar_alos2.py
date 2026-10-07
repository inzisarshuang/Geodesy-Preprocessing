"""Behavior tests for safe ALOS-2/PALSAR product adaptation."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from types import MappingProxyType

from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.adapters.alos2 import ALOS2Adapter
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.adapters.base import safe_extract_zip
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import OtherSarSlcCase
from fixtures.other_sar import alos_symlink_mode, write_alos_zip


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


class ALOS2AdapterTests(unittest.TestCase):
    """Exercise discovery, safety checks, and literal GAMMA plans."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "RAW"
        self.raw.mkdir()
        self.slc = self.root / "SLC"
        self.adapter = ALOS2Adapter()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(self, polarizations: tuple[str, ...] = ("HH",)) -> OtherSarSlcCase:
        return OtherSarSlcCase(
            name="main",
            sensor="alos2",
            raw_dir=self.raw,
            slc_dir=self.slc,
            images_file=self.root / "images_alos2",
            polarizations=polarizations,
            range_looks=10,
            azimuth_looks=10,
            commands=MappingProxyType({"import_command": "par_EORC_PALSAR"}),
            workflow_task="PRE_OTHER_SAR_SLC",
        )

    def test_summary_and_workreport_dates_are_discovered(self) -> None:
        write_alos_zip(self.raw / "a.zip", date="20260922", metadata_name="summary.txt")
        write_alos_zip(self.raw / "nested/b.zip", date="20260923", metadata_name="workreport")

        products = self.adapter.discover(self._case())

        self.assertEqual(tuple(product.date for product in products), ("20260922", "20260923"))
        self.assertEqual(tuple(product.polarizations for product in products), (("HH",), ("HH",)))

    def test_all_polarizations_build_exact_commands_and_legacy_paths(self) -> None:
        archive = write_alos_zip(
            self.raw / "quad.zip", polarizations=("HH", "HV", "VH", "VV")
        )
        before = (_sha256(archive), archive.stat().st_mtime_ns)
        case = self._case(("HH", "HV", "VH", "VV"))

        product = self.adapter.discover(case)[0]
        staging = self.root / ".stage"
        staging.mkdir()
        prepared = self.adapter.prepare(product, staging)
        plan = self.adapter.build_scene_plan(case, prepared)

        expected_commands = []
        for polarization in ("HH", "HV", "VH", "VV"):
            output_root = staging / "products" / polarization / "20260922"
            expected_commands.append(
                (
                    "par_EORC_PALSAR",
                    str(staging / "vendor/SCENE/LED-TEST"),
                    str(output_root / "20260922.slc.par"),
                    str(staging / f"vendor/SCENE/IMG-{polarization}-TEST"),
                    str(output_root / "20260922.slc"),
                )
            )
        self.assertEqual(tuple(command.argv for command in plan.commands), tuple(expected_commands))
        self.assertEqual(
            tuple(output.destination for output in plan.outputs),
            tuple(
                path
                for polarization in ("HH", "HV", "VH", "VV")
                for path in (
                    self.slc / f"{polarization}_SLC/20260922/20260922.slc",
                    self.slc / f"{polarization}_SLC/20260922/20260922.slc.par",
                )
            ),
        )
        self.assertEqual((_sha256(archive), archive.stat().st_mtime_ns), before)

    def test_empty_legacy_selection_uses_every_available_polarization(self) -> None:
        write_alos_zip(
            self.raw / "quad.zip", polarizations=("HH", "HV", "VH", "VV")
        )
        case = self._case(())

        product = self.adapter.discover(case)[0]
        staging = self.root / ".stage"
        staging.mkdir()
        prepared = self.adapter.prepare(product, staging)
        plan = self.adapter.build_scene_plan(case, prepared)

        self.assertEqual(
            tuple(output.polarization for output in plan.outputs[::2]),
            ("HH", "HV", "VH", "VV"),
        )

    def test_duplicate_dates_ambiguous_leader_and_missing_pol_fail_discovery(self) -> None:
        write_alos_zip(self.raw / "a.zip", date="20260922")
        write_alos_zip(self.raw / "b.zip", date="20260922")
        with self.assertRaisesRegex(ValueError, "duplicate acquisition date"):
            self.adapter.discover(self._case())

        (self.raw / "b.zip").unlink()
        with zipfile.ZipFile(self.raw / "a.zip", "a") as archive:
            archive.writestr("SCENE/LED-SECOND", b"leader")
        with self.assertRaisesRegex(ValueError, "exactly one leader"):
            self.adapter.discover(self._case())

        (self.raw / "a.zip").unlink()
        write_alos_zip(self.raw / "a.zip", polarizations=("HH",))
        with self.assertRaisesRegex(ValueError, "requested polarization HV"):
            self.adapter.discover(self._case(("HH", "HV")))

    def test_unsafe_zip_members_are_rejected_before_extraction(self) -> None:
        cases = (
            ("../escape", None),
            ("/absolute", None),
            ("C:/drive-path", None),
            ("SCENE/link", alos_symlink_mode()),
        )
        for member, mode in cases:
            with self.subTest(member=member):
                archive = self.root / f"unsafe-{len(member)}-{abs(hash(member))}.zip"
                write_alos_zip(
                    archive,
                    extra_members=((member, b"payload", mode),),
                )
                destination = self.root / f"extract-{abs(hash(member))}"
                with self.assertRaisesRegex(ValueError, "unsafe|special"):
                    safe_extract_zip(archive, destination)
                self.assertFalse((self.root / "escape").exists())

    def test_duplicate_members_are_rejected(self) -> None:
        archive = self.root / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("SCENE/file", b"one")
                output.writestr("SCENE/file", b"two")
        with self.assertRaisesRegex(ValueError, "duplicate ZIP member"):
            safe_extract_zip(archive, self.root / "extract")


if __name__ == "__main__":
    unittest.main()
