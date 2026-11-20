"""Tests for deterministic signatures and atomic GSLC directory publication."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from geodesy_preprocessing.sar.gslc_subsetting.nisar.publication import (
    atomic_publish_gslc,
    gslc_input_signature,
)
from tests.fixtures.nisar_gslc_plan import make_gslc_plan


class NisarGslcPublicationTests(unittest.TestCase):
    """Keep signatures stable and replace completed output transactionally."""

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

    def test_atomic_publish_requires_same_parent_and_replaces_destination(self) -> None:
        parent = self.plan.case.output_dir.parent
        parent.mkdir(parents=True)
        staging = parent / ".STACK.test.stage"
        staging.mkdir()
        (staging / "marker").write_text("complete", encoding="utf-8")

        atomic_publish_gslc(staging, self.plan.case.output_dir)

        self.assertEqual((self.plan.case.output_dir / "marker").read_text(), "complete")
        second = parent / ".STACK.second.stage"
        second.mkdir()
        (second / "marker").write_text("replacement", encoding="utf-8")
        atomic_publish_gslc(second, self.plan.case.output_dir)
        self.assertEqual(
            (self.plan.case.output_dir / "marker").read_text(encoding="utf-8"),
            "replacement",
        )
        self.assertFalse((parent / f".{self.plan.case.output_dir.name}.backup").exists())

        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        with self.assertRaisesRegex(ValueError, "same parent"):
            atomic_publish_gslc(elsewhere, parent / "other")


if __name__ == "__main__":
    unittest.main()
