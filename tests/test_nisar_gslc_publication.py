"""Tests for deterministic plan records and atomic GSLC directory publication."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from geodesy_preprocessing.sar.gslc_subsetting.nisar.publication import (
    atomic_publish_gslc,
    gslc_input_signature,
    publish_gslc_plan,
)
from tests.fixtures.nisar_gslc_plan import make_gslc_plan


class NisarGslcPublicationTests(unittest.TestCase):
    """Keep plan-only output separate and never replace an official result silently."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = make_gslc_plan(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_input_signature_is_deterministic_and_covers_science_choices(self) -> None:
        first = gslc_input_signature(self.plan)
        second = gslc_input_signature(self.plan)
        changed_case = replace(self.plan.case, buffer_km=1.0)

        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, gslc_input_signature(replace(self.plan, case=changed_case)))
        self.assertNotEqual(
            first,
            gslc_input_signature(
                replace(self.plan, selected_polarizations=("HV",))
            ),
        )

    def test_publish_plan_writes_log_artifact_without_creating_result(self) -> None:
        path = publish_gslc_plan(self.plan)

        self.assertEqual(
            path,
            self.plan.case.project_root / "LOG/PRE/NISAR/GSLC/la/plan.json",
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["status"], "planned")
        self.assertEqual(document["input_signature"], gslc_input_signature(self.plan))
        self.assertEqual(document["window"]["shape"], [3, 3])
        self.assertEqual(document["assets"], 2)
        self.assertFalse(self.plan.case.output_dir.exists())

    def test_atomic_publish_requires_same_parent_and_absent_destination(self) -> None:
        parent = self.plan.case.output_dir.parent
        parent.mkdir(parents=True)
        staging = parent / ".STACK.test.stage"
        staging.mkdir()
        (staging / "marker").write_text("complete", encoding="utf-8")

        atomic_publish_gslc(staging, self.plan.case.output_dir)

        self.assertEqual((self.plan.case.output_dir / "marker").read_text(), "complete")
        second = parent / ".STACK.second.stage"
        second.mkdir()
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            atomic_publish_gslc(second, self.plan.case.output_dir)
        self.assertTrue(second.exists())

        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        with self.assertRaisesRegex(ValueError, "same parent"):
            atomic_publish_gslc(elsewhere, parent / "other")


if __name__ == "__main__":
    unittest.main()
