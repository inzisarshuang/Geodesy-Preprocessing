"""Tests for transactional GAMMA registered-stack crop execution."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import tests.test_gamma_crop_plan as crop_plan_fixtures
from geodesy_preprocessing.sar.common.stack_manifest import read_stack_manifest
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.config import load_crop_config
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.masking import (
    CoverageReport,
    CropGeometryResult,
)
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.plan import build_crop_plan
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.runner import run_crop_stack
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.window import (
    CropWindow,
    PixelBounds,
)


def fake_geometry(plan, work_dir: Path, *, runner=None) -> CropGeometryResult:
    work_dir.mkdir(parents=True, exist_ok=False)
    geojson = work_dir / "buffered_aoi.geojson"
    map_tif = work_dir / "aoi_mask_map.tif"
    radar_bmp = work_dir / "aoi_mask_radar.bmp"
    for path in (geojson, map_tif, radar_bmp):
        path.write_bytes(b"artifact")
    return CropGeometryResult(
        CoverageReport(10, 10, 1.0, 20, 19, 0.95),
        CropWindow(2, 6, 2, 4, PixelBounds(1, 1, 4, 3)),
        geojson,
        map_tif,
        radar_bmp,
    )


class FakeSlcRunner:
    """Create deterministic SLC_copy and rasSLC outputs without GAMMA."""

    def __init__(self, *, fail_on_copy: int | None = None) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.copy_count = 0
        self.fail_on_copy = fail_on_copy

    def run(self, command):
        command = tuple(command)
        self.commands.append(command)
        if command[0] == "SLC_copy":
            self.copy_count += 1
            if self.copy_count == self.fail_on_copy:
                raise RuntimeError("forced SLC_copy failure")
            source_par = Path(command[2]).read_text(encoding="ascii")
            output, parameter = Path(command[3]), Path(command[4])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"cropped-complex")
            date_line = next(line for line in source_par.splitlines() if line.startswith("date:"))
            pol_line = next(line for line in source_par.splitlines() if line.startswith("polarization:"))
            image_line = next(line for line in source_par.splitlines() if line.startswith("image_format:"))
            parameter.write_text(
                f"{date_line}\nrange_samples: {command[8]}\nazimuth_lines: {command[10]}\n"
                f"range_looks: 1\nazimuth_looks: 1\n{image_line}\n{pol_line}\n",
                encoding="ascii",
            )
        elif command[0] == "rasSLC":
            Path(command[9]).write_bytes(b"bitmap")
        else:
            raise AssertionError(command)
        return SimpleNamespace(stdout="stdout-detail\n", stderr="stderr-detail\n")


class GammaCropRunnerTests(unittest.TestCase):
    """Crop every selected asset with one window and publish only complete stacks."""

    def _plan(self, root: Path, *, image_format: str = "FCOMPLEX"):
        config_path, _ = crop_plan_fixtures.GammaCropPlanTests()._project(root)
        config = load_crop_config(config_path, "main")
        if image_format != "FCOMPLEX":
            for parameter in config.registered_stack.parent.glob("RSLC/*.VV.rslc.par"):
                parameter.write_text(
                    parameter.read_text().replace("image_format: FCOMPLEX", f"image_format: {image_format}")
                )
        return build_crop_plan(config)

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_crops_all_assets_with_one_window_and_publishes_manifest_logs_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._plan(Path(directory))
            before = {}
            for asset in plan.assets:
                path = plan.source_root / asset.data_path
                digest = self._digest(path)
                before[path] = (path.stat(), digest)
            runner = FakeSlcRunner()
            events: list[str] = []

            summary = run_crop_stack(
                plan,
                command_runner=runner,
                geometry_builder=fake_geometry,
                progress_sink=events.append,
            )

            self.assertEqual((summary.total, summary.completed, summary.skipped), (2, 2, 0))
            copy_commands = [command for command in runner.commands if command[0] == "SLC_copy"]
            self.assertEqual(len(copy_commands), 2)
            self.assertTrue(all(command[5:11] == ("1", "1.0", "2", "6", "2", "4") for command in copy_commands))
            manifest = read_stack_manifest(plan.output_dir / "registered_stack.json")
            self.assertEqual(manifest.dates, plan.manifest.dates)
            self.assertEqual(manifest.polarizations, ("VV",))
            self.assertEqual((plan.output_dir / "images").read_text(), "20240101\n20240113\n")
            self.assertEqual(len(list((plan.output_dir / "quicklooks").glob("*.bmp"))), 2)
            log_root = plan.config.project_root / "LOG/PRE/GAMMA/gamma_sar_rslc_crop/main"
            self.assertIn("stdout-detail", (log_root / "stdout.log").read_text())
            self.assertIn("stderr-detail", (log_root / "stderr.log").read_text())
            self.assertEqual(sum('"event":"item_finished"' in event for event in events), 2)
            for path, (stat, digest) in before.items():
                self.assertEqual(path.stat(), stat)
                self.assertEqual(self._digest(path), digest)

    def test_scomplex_uses_format_preserving_fcase_four(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._plan(Path(directory), image_format="SCOMPLEX")
            runner = FakeSlcRunner()
            run_crop_stack(
                plan,
                command_runner=runner,
                geometry_builder=fake_geometry,
                progress_sink=lambda line: None,
            )
            copies = [command for command in runner.commands if command[0] == "SLC_copy"]
            self.assertTrue(all(command[5] == "4" for command in copies))

    def test_case_layout_writes_crop_logs_below_case_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = self._plan(Path(directory))
            case_root = base.config.project_root / "S1/ASCENDING_128"
            config = dataclasses.replace(
                base.config,
                case_root=case_root,
                output_dir=case_root / "PRE/GAMMA/RSLC_SUBSET",
            )
            plan = build_crop_plan(config)

            run_crop_stack(
                plan,
                command_runner=FakeSlcRunner(),
                geometry_builder=fake_geometry,
                progress_sink=lambda line: None,
            )

            log = (
                case_root
                / "LOG/PRE/GAMMA/gamma_sar_rslc_crop/main/stdout.log"
            )
            self.assertTrue(log.is_file())

    def test_all_polarizations_publish_explicit_asset_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = self._plan(Path(directory))
            plan = build_crop_plan(dataclasses.replace(base.config, polarizations=None))
            runner = FakeSlcRunner()
            summary = run_crop_stack(
                plan,
                command_runner=runner,
                geometry_builder=fake_geometry,
                progress_sink=lambda line: None,
            )
            manifest = read_stack_manifest(plan.output_dir / "registered_stack.json")
            self.assertEqual((summary.total, summary.completed), (4, 4))
            self.assertEqual(manifest.polarizations, ("VV", "VH"))
            self.assertEqual(
                {asset.data_path.name for asset in manifest.assets},
                {
                    "20240101.VV.rslc",
                    "20240101.VH.rslc",
                    "20240113.VV.rslc",
                    "20240113.VH.rslc",
                },
            )

    def test_failure_or_concurrent_target_never_publishes_partial_stack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self._plan(Path(directory))
            events: list[str] = []
            with self.assertRaisesRegex(RuntimeError, "forced"):
                run_crop_stack(
                    plan,
                    command_runner=FakeSlcRunner(fail_on_copy=2),
                    geometry_builder=fake_geometry,
                    progress_sink=events.append,
                )
            self.assertFalse(plan.output_dir.exists())
            self.assertEqual(list(plan.output_dir.parent.glob(f".{plan.output_dir.name}.*.stage")), [])
            self.assertTrue(
                any(
                    '"event":"item_finished"' in event
                    and '"item":"20240113:VV"' in event
                    and '"status":"failed"' in event
                    for event in events
                )
            )

            def concurrent_geometry(current_plan, work_dir, *, runner=None):
                result = fake_geometry(current_plan, work_dir, runner=runner)
                current_plan.output_dir.mkdir()
                (current_plan.output_dir / "foreign").write_text("keep")
                return result

            with self.assertRaises(FileExistsError):
                run_crop_stack(
                    plan,
                    command_runner=FakeSlcRunner(),
                    geometry_builder=concurrent_geometry,
                    progress_sink=lambda line: None,
                )
            self.assertEqual((plan.output_dir / "foreign").read_text(), "keep")

    def test_identical_result_skips_conflict_stops_and_force_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan(root)
            run_crop_stack(
                plan,
                command_runner=FakeSlcRunner(),
                geometry_builder=fake_geometry,
                progress_sink=lambda line: None,
            )
            existing_plan = build_crop_plan(plan.config)
            skipped = run_crop_stack(
                existing_plan,
                command_runner=FakeSlcRunner(fail_on_copy=1),
                geometry_builder=fake_geometry,
                progress_sink=lambda line: None,
            )
            self.assertEqual((skipped.completed, skipped.skipped), (0, 2))

            changed = build_crop_plan(
                dataclasses.replace(plan.config, buffer_km=6.0)
            )
            with self.assertRaisesRegex(FileExistsError, "different"):
                run_crop_stack(
                    changed,
                    command_runner=FakeSlcRunner(),
                    geometry_builder=fake_geometry,
                    progress_sink=lambda line: None,
                )
            replaced = run_crop_stack(
                changed,
                force=True,
                command_runner=FakeSlcRunner(),
                geometry_builder=fake_geometry,
                progress_sink=lambda line: None,
            )
            self.assertEqual(replaced.completed, 2)
            provenance = json.loads((changed.output_dir / "crop_window.json").read_text())
            self.assertEqual(provenance["buffer_km"], 6.0)
            self.assertEqual(
                provenance["aoi_sha256"],
                hashlib.sha256(changed.config.kml_file.read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
