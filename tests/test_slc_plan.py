"""Tests for deterministic Sentinel-1 SLC batch planning."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.tops.plan import (
    SlcBatchPlan,
    SlcItemPlan,
    build_slc_plan,
    read_image_items,
)


class ImageListTests(unittest.TestCase):
    """Reject image items that could reorder or escape output paths."""

    def test_reads_first_column_in_order_and_ignores_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "images"
            path.write_text("20240929 note\n\n20240905\textra\n", encoding="utf-8")

            self.assertEqual(read_image_items(path), ("20240929", "20240905"))

    def test_rejects_empty_duplicate_and_unsafe_items(self) -> None:
        fixtures = {
            "empty": "\n",
            "duplicate": "20240905\n20240905\n",
            "unsafe": "../20240905\n",
        }
        for name, content in fixtures.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "images"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_image_items(path)

    def test_models_are_immutable(self) -> None:
        item = SlcItemPlan(
            item="20240905",
            sources=(Path("a.SAFE"),),
            output_dir=Path("SLC/20240905"),
            polarization="vv",
            skip=False,
            skip_reason=None,
        )
        with self.assertRaises(FrozenInstanceError):
            item.skip = True  # type: ignore[misc]


class SlcPlanDiscoveryTests(unittest.TestCase):
    """Build ordered read-only SAFE/ZIP plans with correct skip semantics."""

    def _layout(self, root: Path, workflow_flag: int = 1) -> dict[str, object]:
        root.mkdir(parents=True, exist_ok=True)
        raw = root / "RAW"
        raw.mkdir()
        slc = root / "SLC"
        images = root / "images_154"
        images.write_text("20240905\n20240917\n", encoding="utf-8")
        burst = root / "ref_burst_number_table_154"
        burst.write_text("iw1_number_of_bursts: 1\n", encoding="ascii")
        suffix = ".SAFE" if workflow_flag == 1 else ".zip"
        for name in (
            f"S1B_B_20240905{suffix}",
            f"S1A_A_20240905{suffix}",
            f"S1A_20240917{suffix}",
        ):
            path = raw / name
            path.mkdir() if workflow_flag == 1 else path.write_bytes(b"zip")
        return {
            "raw_dir": raw,
            "slc_dir": slc,
            "opod_dir": "-",
            "images_file": images,
            "burst_table": burst,
            "range_looks": 4,
            "azimuth_looks": 1,
            "polarization": "-",
            "workflow_flag": workflow_flag,
        }

    def test_safe_sources_are_sorted_and_image_order_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            arguments = self._layout(Path(temporary_directory), workflow_flag=1)

            plan = build_slc_plan(**arguments)

            self.assertIsInstance(plan, SlcBatchPlan)
            self.assertEqual([item.item for item in plan.items], ["20240905", "20240917"])
            self.assertEqual(
                [path.name for path in plan.items[0].sources],
                ["S1A_A_20240905.SAFE", "S1B_B_20240905.SAFE"],
            )
            self.assertEqual(plan.workflow, "safe")
            self.assertEqual(plan.items[0].polarization, "vv")

    def test_zip_plan_and_non_vv_skip_use_requested_polarization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            arguments = self._layout(Path(temporary_directory), workflow_flag=0)
            arguments["polarization"] = "VH"
            output = Path(arguments["slc_dir"]) / "20240905"
            output.mkdir(parents=True)
            (output / "20240905.vh.mli").write_bytes(b"complete")

            plan = build_slc_plan(**arguments)

            self.assertEqual(plan.workflow, "zip")
            self.assertEqual(plan.items[0].polarization, "vh")
            self.assertTrue(plan.items[0].skip)
            self.assertEqual(plan.items[0].skip_reason, "requested MLI already exists")
            self.assertFalse(plan.items[1].skip)

    def test_missing_source_and_invalid_control_values_fail_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            arguments = self._layout(Path(temporary_directory), workflow_flag=1)
            for path in Path(arguments["raw_dir"]).glob("*20240917*.SAFE"):
                path.rmdir()
            with self.assertRaisesRegex(FileNotFoundError, "20240917"):
                build_slc_plan(**arguments)

            arguments = self._layout(Path(temporary_directory) / "second", workflow_flag=1)
            for field, value in (
                ("range_looks", 0),
                ("azimuth_looks", -1),
                ("workflow_flag", 2),
            ):
                broken = dict(arguments)
                broken[field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    build_slc_plan(**broken)


if __name__ == "__main__":
    unittest.main()
