"""Tests for staged Sentinel-1 TOPS stack coregistration."""

from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_plan import build_coreg_plan
from geodesy_preprocessing.sar.common.stack_manifest import read_stack_manifest
from geodesy_preprocessing.sar.rslc_registration.common import RegistrationResult


class CoregRunnerTests(unittest.TestCase):
    """Exercise read-only views, owned-file organization, progress, and publication."""

    def _module(self):
        try:
            return importlib.import_module(
                "geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_runner"
            )
        except ModuleNotFoundError:
            self.fail("coreg_runner module has not been implemented")

    def _plan(self, root: Path):
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
        images = root / "images"
        images.write_text("20240905\n20240917\n", encoding="ascii")
        dem = root / "DEM_geocode"
        dem.mkdir()
        (dem / "20240905.hgt_sim").write_bytes(b"height")
        return build_coreg_plan(slc, dem, root / "RSLC", "20240905", images, 7, 2)

    def _write_raw_outputs(self, staging: Path) -> None:
        for date in ("20240905", "20240917"):
            for suffix in (".rslc", ".rslc.par", ".vv.iw1.rslc", ".vv.iw1.rslc.par", ".vv.iw1.rslc.tops_par"):
                (staging / f"{date}{suffix}").write_bytes(b"product")
        (staging / "ID.coreg_quality").write_bytes(b"summary")
        (staging / "20240905_20240917.coreg_quality").write_bytes(b"quality")
        (staging / "20240905_20240917.results").write_bytes(b"results")
        (staging / "20240905_20240917.diff.bmp").write_bytes(b"preview")
        (staging / "20240917.rslc.iw1.tmp").write_bytes(b"intermediate")
        (staging / "20240917.mli").write_bytes(b"intermediate")
        (staging / "20240905.rmli.par").write_bytes(b"root-product")
        (staging / "20240905.rmli.bmp").write_bytes(b"root-preview")
        temporary = staging / "tmp_dir_owned"
        temporary.mkdir()
        (temporary / "scratch").write_bytes(b"scratch")

    def test_runner_uses_isolated_slc_view_and_organizes_only_owned_outputs(self) -> None:
        module = self._module()

        outer = self

        class SimulatedRunner(module.CoregRunner):
            def _execute(self, command, cwd, *, plan, item_sink):
                outer._write_raw_outputs(cwd)
                item_sink("20240905")
                item_sink("20240917")
                self.command = tuple(command)
                return module.CoregCommandResult("coregister", tuple(command), 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)
            before = tuple(sorted(path.relative_to(plan.slc_dir) for path in plan.slc_dir.rglob("*")))
            staging = root / "stage"
            staging.mkdir()
            completed: list[str] = []
            runner = SimulatedRunner()

            result = runner.run_stack(plan, staging, item_sink=completed.append)

            self.assertEqual(result.stage, "coregister")
            self.assertEqual(completed, ["20240905", "20240917"])
            self.assertIn("--npoly", runner.command)
            self.assertIn("4", runner.command)
            self.assertNotIn("--sec_ref", runner.command)
            self.assertFalse((staging / "slc_view").exists())
            self.assertFalse((staging / "tmp_dir_owned").exists())
            self.assertTrue((staging / "RSLC" / "20240917.rslc").is_file())
            self.assertTrue((staging / "coreg_quality" / "1ID.coreg_quality").is_file())
            self.assertTrue((staging / "diff_bmp" / "20240905_20240917.diff.bmp").is_file())
            self.assertTrue((staging / "sup_mtls" / "20240917.rslc.iw1.tmp").is_file())
            self.assertTrue((staging / "20240905.rmli.par").is_file())
            self.assertEqual(
                before,
                tuple(sorted(path.relative_to(plan.slc_dir) for path in plan.slc_dir.rglob("*"))),
            )

    def test_runner_copies_tops_parameters_that_gamma_may_rewrite(self) -> None:
        module = self._module()
        outer = self

        class MetadataWritingRunner(module.CoregRunner):
            def _execute(self, command, cwd, *, plan, item_sink):
                view = Path(command[1]).parent
                mutable = view / "20240917.vv.iw1.slc.tops_par"
                self.assert_is_copy = not mutable.is_symlink()
                mutable.write_bytes(b"gamma-updated")
                outer._write_raw_outputs(cwd)
                item_sink("20240905")
                item_sink("20240917")
                return module.CoregCommandResult("coregister", tuple(command), 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)
            source = (
                plan.slc_dir
                / "20240917"
                / "20240917.vv.iw1.slc.tops_par"
            )
            original = source.read_bytes()
            source.chmod(0o444)
            staging = root / "stage"
            staging.mkdir()
            runner = MetadataWritingRunner()

            runner.run_stack(plan, staging, item_sink=lambda date: None)

            self.assertTrue(runner.assert_is_copy)
            self.assertEqual(source.read_bytes(), original)

    def test_batch_wrapper_publishes_complete_stack_and_reports_each_scene(self) -> None:
        module = self._module()
        outer = self

        class ProductRunner:
            def run_stack(self, plan, staging, *, item_sink):
                outer._write_raw_outputs(staging)
                module.organize_coreg_outputs(plan, staging)
                item_sink("20240905")
                item_sink("20240917")
                return module.CoregCommandResult("coregister", ("simulated",), 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)
            events: list[str] = []

            result = module.run_coreg(plan, runner=ProductRunner(), progress_sink=events.append)

            self.assertEqual((result.success, result.skipped, result.failed), (2, 0, 0))
            self.assertIsInstance(result.registration, RegistrationResult)
            self.assertEqual(
                result.registration.manifest_path,
                plan.output_dir / "registered_stack.json",
            )
            manifest = read_stack_manifest(result.registration.manifest_path)
            self.assertEqual(manifest.reference_date, "20240905")
            self.assertEqual(manifest.dates, ("20240905", "20240917"))
            self.assertEqual(manifest.backend.name, "gamma")
            self.assertEqual(manifest.schema_version, 2)
            self.assertEqual(
                tuple((asset.date, asset.polarization) for asset in manifest.assets),
                (("20240905", "VV"), ("20240917", "VV")),
            )
            for asset in manifest.assets:
                self.assertTrue((plan.output_dir / asset.data_path).is_file())
                self.assertTrue((plan.output_dir / asset.parameter_path).is_file())
            self.assertTrue(all(path.is_file() for path in plan.expected_outputs))
            payloads = [json.loads(line.removeprefix("@@GEODESY_PROGRESS ")) for line in events]
            finished = [payload for payload in payloads if payload["event"] == "item_finished"]
            self.assertEqual([payload["item"] for payload in finished], ["20240905", "20240917"])
            self.assertEqual([payload["status"] for payload in finished], ["success", "success"])

    def test_skip_preserves_complete_legacy_stack_without_adding_manifest(self) -> None:
        module = self._module()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = self._plan(root)
            for expected in initial.expected_outputs:
                expected.parent.mkdir(parents=True, exist_ok=True)
                expected.write_bytes(b"existing-product")
            plan = replace(initial, skip=True)
            before = tuple(
                sorted(path.relative_to(plan.output_dir) for path in plan.output_dir.rglob("*"))
            )

            result = module.run_coreg(plan, progress_sink=lambda line: None)

            self.assertEqual((result.success, result.skipped, result.failed), (0, 2, 0))
            self.assertIsNone(result.registration)
            self.assertFalse((plan.output_dir / "registered_stack.json").exists())
            self.assertEqual(
                before,
                tuple(
                    sorted(
                        path.relative_to(plan.output_dir)
                        for path in plan.output_dir.rglob("*")
                    )
                ),
            )

    def test_concurrent_destination_is_preserved_and_staging_is_removed(self) -> None:
        module = self._module()

        class RacingRunner:
            def run_stack(self, plan, staging, *, item_sink):
                plan.output_dir.mkdir()
                return module.CoregCommandResult("coregister", ("simulated",), 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)

            with self.assertRaisesRegex(FileExistsError, "appeared during execution"):
                module.run_coreg(plan, runner=RacingRunner(), progress_sink=lambda line: None)

            self.assertTrue(plan.output_dir.is_dir())
            self.assertFalse(any(root.glob(".RSLC.*.stage")))


if __name__ == "__main__":
    unittest.main()
