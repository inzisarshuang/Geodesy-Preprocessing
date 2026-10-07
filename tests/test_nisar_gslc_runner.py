"""Tests for complete, repeatable, transactional NISAR GSLC stack publication."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.common.progress import PROGRESS_PREFIX
from geodesy_preprocessing.sar.common.models import GeometryType
from geodesy_preprocessing.sar.common.stack_manifest import read_stack_manifest
from geodesy_preprocessing.sar.gslc_subsetting.nisar import runner
from geodesy_preprocessing.sar.gslc_subsetting.nisar.publication import (
    gslc_input_signature,
)
from tests.fixtures.nisar_gslc_plan import make_gslc_plan


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NisarGslcRunnerTests(unittest.TestCase):
    """Publish all-or-nothing stacks with readable metadata and safe reruns."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = make_gslc_plan(self.root, polarizations=("HH", "HV"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_run_publishes_complete_schema_v2_stack_and_progress(self) -> None:
        progress: list[str] = []

        summary = runner.run_nisar_gslc_stack(self.plan, progress_sink=progress.append)

        self.assertEqual((summary.total, summary.completed, summary.skipped, summary.failed), (4, 4, 0, 0))
        output = self.plan.case.output_dir
        for name in (
            "registered_stack.json",
            "images",
            "crop_window.json",
            "quality.json",
            "provenance.json",
        ):
            self.assertTrue((output / name).is_file(), name)
        for asset in self.plan.assets:
            data = output / "complex" / asset.polarization / asset.date / f"{asset.date}.tif"
            parameter = output / "metadata" / asset.polarization / asset.date / f"{asset.date}.json"
            quicklook = output / "quicklooks" / asset.polarization / f"{asset.date}.png"
            self.assertTrue(data.is_file())
            self.assertTrue(parameter.is_file())
            self.assertTrue(quicklook.is_file())
            metadata = json.loads(parameter.read_text(encoding="utf-8"))
            self.assertEqual(metadata["dataset_path"], asset.channel.dataset_path)
            self.assertEqual(metadata["dtype"], "complex64")
            self.assertEqual(metadata["shape"], [3, 3])
            self.assertEqual(metadata["epsg"], 32630)
            self.assertEqual(metadata["source_sha256"], asset.source_sha256)
            self.assertEqual(metadata["output_sha256"], _sha256(data))
        manifest = read_stack_manifest(output / "registered_stack.json")
        self.assertEqual(manifest.geometry_type, GeometryType.GEOCODED_GRID)
        self.assertEqual((manifest.backend.name, manifest.backend.version), ("nisar-native", "1.4.0"))
        self.assertEqual(manifest.source_product_type, "GSLC")
        self.assertEqual(manifest.frequencies, ("LA",))
        self.assertEqual(manifest.polarizations, ("HH", "HV"))
        self.assertEqual(manifest.pixel_spacing, (20.0, -20.0))
        self.assertEqual(manifest.origin, (500030.0, 4200090.0))
        self.assertEqual(manifest.shape, (3, 3))
        self.assertEqual((manifest.track, manifest.frame), ("87", "22"))
        self.assertEqual((output / "images").read_text(), "20260105\n20260117\n")
        crop = json.loads((output / "crop_window.json").read_text())
        self.assertEqual(crop["input_signature"], gslc_input_signature(self.plan))
        quality = json.loads((output / "quality.json").read_text())
        self.assertEqual(len(quality["assets"]), 4)
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertEqual(provenance["backend"], "nisar-native")
        events = [json.loads(line.removeprefix(PROGRESS_PREFIX)) for line in progress]
        self.assertEqual(events[0]["event"], "batch_started")
        self.assertEqual(events[-1]["status"], "complete")
        self.assertEqual(
            len([event for event in events if event["event"] == "item_finished" and event["status"] == "complete"]),
            4,
        )

    def test_complete_identical_rerun_skips_but_stale_and_incomplete_outputs_stop(self) -> None:
        runner.run_nisar_gslc_stack(self.plan, progress_sink=lambda line: None)
        second = runner.run_nisar_gslc_stack(self.plan, progress_sink=lambda line: None)
        self.assertEqual((second.completed, second.skipped), (0, 4))

        changed = replace(self.plan, case=replace(self.plan.case, buffer_km=1.0))
        with self.assertRaisesRegex(FileExistsError, "different inputs"):
            runner.run_nisar_gslc_stack(changed, progress_sink=lambda line: None)

        other = make_gslc_plan(self.root / "other")
        other.case.output_dir.mkdir(parents=True)
        (other.case.output_dir / "partial").write_text("bad", encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "incomplete"):
            runner.run_nisar_gslc_stack(other, progress_sink=lambda line: None)

    def test_mid_stack_failure_publishes_nothing_preserves_sources_and_redacts_log(self) -> None:
        before = {asset.source_file: _sha256(asset.source_file) for asset in self.plan.assets}
        real_writer = runner.write_gslc_complex_geotiff
        calls = 0

        def failing_writer(asset, destination, block_rows=512):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError(
                    "service failed password=VERY-SECRET token=TOKEN-SECRET "
                    "https://earth-user:earth-pass@example.test/file?token=QUERY-SECRET"
                )
            return real_writer(asset, destination, block_rows=block_rows)

        with mock.patch.object(runner, "write_gslc_complex_geotiff", side_effect=failing_writer):
            with self.assertRaises(RuntimeError):
                runner.run_nisar_gslc_stack(self.plan, progress_sink=lambda line: None)

        self.assertFalse(self.plan.case.output_dir.exists())
        failures = list(self.plan.case.output_dir.parent.glob("STACK.failed-logs-*"))
        self.assertEqual(len(failures), 1)
        text = (failures[0] / "error.json").read_text(encoding="utf-8")
        for secret in ("VERY-SECRET", "TOKEN-SECRET", "QUERY-SECRET", "earth-user", "earth-pass"):
            self.assertNotIn(secret, text)
        self.assertIn("<redacted>", text)
        after = {asset.source_file: _sha256(asset.source_file) for asset in self.plan.assets}
        self.assertEqual(before, after)
        self.assertFalse(any(self.plan.case.output_dir.parent.glob(".STACK.*.stage")))


if __name__ == "__main__":
    unittest.main()
