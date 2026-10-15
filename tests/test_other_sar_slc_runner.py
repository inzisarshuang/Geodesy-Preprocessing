"""Integration tests for the unified Other-SAR SLC runner."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import dataclasses
from pathlib import Path
from types import MappingProxyType

from fixtures.other_sar import (
    write_alos_zip,
    write_cosmo_product,
    write_lt1_product,
    write_rsat2_product,
    write_saocom_product,
)
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import OtherSarSlcCase
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.plan import build_slc_plan
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.runner import (
    OtherSarCommandError,
    run_slc_batch,
)
from tests.test_nisar_hdf5 import write_rslc


FIXTURE = Path(__file__).parent / "fixtures/fake_other_sar_command.py"


def _snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


class OtherSarSlcRunnerTests(unittest.TestCase):
    """Run every adapter through real subprocess and publication boundaries."""

    def _case(
        self,
        root: Path,
        sensor: str,
        polarizations: tuple[str, ...],
        commands: dict[str, str],
    ) -> OtherSarSlcCase:
        adapter_options = (
            MappingProxyType(
                {
                    "band": "L",
                    "frequency": "A",
                    "radiometric_calibration": "1",
                    "noise_subtraction": "0",
                }
            )
            if sensor == "nisar"
            else MappingProxyType({})
        )
        return OtherSarSlcCase(
            name="main",
            sensor=sensor,
            raw_dir=root / "RAW",
            slc_dir=root / "SLC",
            images_file=root / f"images_{sensor}",
            polarizations=polarizations,
            range_looks=10,
            azimuth_looks=10,
            commands=MappingProxyType(commands),
            workflow_task="PRE_GAMMA_SAR_SLC",
            adapter_options=adapter_options,
        )

    def _fake(self, kind: str) -> str:
        return f"{sys.executable} {FIXTURE} {kind}"

    def test_all_adapters_publish_expected_products_and_preserve_sources(self) -> None:
        definitions = (
            ("alos2", ("HH",), lambda raw: write_alos_zip(raw / "a.zip"), {"import_command": self._fake("alos")}),
            ("csg", ("HH",), lambda raw: write_cosmo_product(raw, sensor="csg", date="20260922"), {"import_command": self._fake("cosmo")}),
            ("csks", ("HH",), lambda raw: write_cosmo_product(raw, sensor="csks", date="20260922"), {"import_command": self._fake("cosmo")}),
            (
                "lt1",
                ("HH",),
                lambda raw: write_lt1_product(raw, date="20260922"),
                {
                    "import_command": self._fake("lt1"),
                    "orbit_filter_command": self._fake("orbit"),
                    "preview_command": self._fake("preview"),
                },
            ),
            ("rsat2", ("HH", "HV"), lambda raw: write_rsat2_product(raw, date="20260922", polarizations=("HH", "HV")), {"import_command": self._fake("rsat2")}),
            ("saocom", ("HH", "HV"), lambda raw: write_saocom_product(raw, date="20260922", pair_names=("A", "B")), {"import_command": self._fake("saocom")}),
            (
                "nisar",
                ("HH",),
                lambda raw: write_rslc(raw / "NISAR_TEST.h5", bands={"L": {"A": ("HH",)}}),
                {"import_command": self._fake("nisar"), "preview_command": self._fake("preview")},
            ),
        )
        for sensor, polarizations, create, commands in definitions:
            with self.subTest(sensor=sensor), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                raw = root / "RAW"
                raw.mkdir()
                create(raw)
                before = _snapshot(raw)
                case = self._case(root, sensor, polarizations, commands)
                events: list[str] = []
                summary = run_slc_batch(build_slc_plan(case), progress_sink=events.append)
                self.assertEqual((summary.total, summary.completed, summary.skipped, summary.failed), (1, 1, 0, 0))
                self.assertEqual(_snapshot(raw), before)
                expected_date = "20260105" if sensor == "nisar" else "20260922"
                self.assertEqual(case.images_file.read_text(), f"{expected_date}\n")
                for polarization in polarizations:
                    search_root = (
                        case.slc_dir
                        if sensor == "lt1"
                        else case.slc_dir / f"{polarization}_SLC"
                    )
                    self.assertTrue(any(search_root.rglob(f"{expected_date}.slc")))
                payloads = [json.loads(line.split(" ", 1)[1]) for line in events]
                self.assertEqual(payloads[0]["status"], "start")
                self.assertEqual(payloads[-1]["status"], "complete")
                log_root = root / f"LOG/PRE/PRE_GAMMA_SAR_SLC/main/{expected_date}"
                self.assertTrue((log_root / "stdout.log").is_file())
                self.assertTrue((log_root / "stderr.log").is_file())
                self.assertEqual(list(root.glob(".*.stage")), [])

    def test_failure_preserves_earlier_scene_and_rerun_skips_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "RAW"
            raw.mkdir()
            write_alos_zip(raw / "a.zip", date="20260922")
            write_alos_zip(raw / "b.zip", date="20260923")
            case = self._case(root, "alos2", ("HH",), {"import_command": self._fake("alos")})
            with self.assertRaises(OtherSarCommandError):
                run_slc_batch(
                    build_slc_plan(case),
                    environment={"FAKE_OTHER_SAR_FAIL_ITEM": "20260923"},
                    progress_sink=lambda line: None,
                )
            first = case.slc_dir / "HH_SLC/20260922/20260922.slc"
            second = case.slc_dir / "HH_SLC/20260923/20260923.slc"
            self.assertTrue(first.is_file())
            self.assertFalse(second.exists())
            summary = run_slc_batch(build_slc_plan(case), progress_sink=lambda line: None)
            self.assertEqual((summary.completed, summary.skipped), (1, 1))

    def test_case_layout_writes_logs_below_case_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            raw = project / "RAW"
            raw.mkdir()
            write_alos_zip(raw / "a.zip", date="20260922")
            case_root = project / "ALOS2/MAIN"
            case = dataclasses.replace(
                self._case(
                    project,
                    "alos2",
                    ("HH",),
                    {"import_command": self._fake("alos")},
                ),
                slc_dir=case_root / "PRE/GAMMA/SLC",
                images_file=case_root / "PRE/GAMMA/SLC/images_alos2",
                case_root=case_root,
            )

            run_slc_batch(build_slc_plan(case), progress_sink=lambda line: None)

            log = (
                case_root
                / "LOG/PRE/GAMMA/PRE_GAMMA_SAR_SLC/main/20260922/stdout.log"
            )
            self.assertTrue(log.is_file())
            self.assertFalse((case_root / "PRE/GAMMA/LOG").exists())

    def test_zero_exit_with_missing_output_publishes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "RAW"
            raw.mkdir()
            write_alos_zip(raw / "a.zip", date="20260922")
            case = self._case(root, "alos2", ("HH",), {"import_command": self._fake("alos")})
            with self.assertRaisesRegex(ValueError, "staged output"):
                run_slc_batch(
                    build_slc_plan(case),
                    environment={"FAKE_OTHER_SAR_MISSING_ITEM": "20260922"},
                    progress_sink=lambda line: None,
                )
            self.assertFalse((case.slc_dir / "HH_SLC/20260922/20260922.slc").exists())
            self.assertEqual(list(root.glob(".*.stage")), [])

    def test_discovery_failure_preserves_existing_images_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "RAW"
            raw.mkdir()
            write_alos_zip(raw / "a.zip", date="20260922")
            write_alos_zip(raw / "b.zip", date="20260922")
            case = self._case(root, "alos2", ("HH",), {"import_command": self._fake("alos")})
            case.images_file.write_text("original\n")
            with self.assertRaisesRegex(ValueError, "duplicate acquisition date"):
                build_slc_plan(case)
            self.assertEqual(case.images_file.read_text(), "original\n")


if __name__ == "__main__":
    unittest.main()
