"""Tests for strict project-local NISAR GSLC stack configuration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.gslc_subsetting.nisar.config import (
    load_nisar_gslc_stack_config,
    select_nisar_gslc_stack_case,
)


class NisarGslcStackConfigTests(unittest.TestCase):
    """Reject ambiguous science choices, unsafe paths, and credential fields."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = self.root / "gslc.cfg"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, replacement: tuple[str, str] | None = None) -> None:
        text = f"""
[project]
root = {self.project}

[defaults]
source_manifest = INPUT/NISAR/GSLC/source_manifest.json
kml_file = INPUT/ROI/area.kml
buffer_km = 5
band = L
polarizations = HH, HV

[case:la]
frequency = A
reference_date = 20260831
output_dir = WORK/NISAR/GSLC/LA/STACK

[case:lb]
frequency = B
polarizations = HH
reference_date = 20260831
output_dir = WORK/NISAR/GSLC/LB/STACK
""".strip()
        if replacement is not None:
            text = text.replace(*replacement)
        self.config.write_text(text + "\n", encoding="utf-8")

    def test_loads_independent_la_and_lb_cases(self) -> None:
        self._write()

        cases = load_nisar_gslc_stack_config(self.config)
        la = select_nisar_gslc_stack_case(cases, "la")
        lb = select_nisar_gslc_stack_case(cases, "lb")

        self.assertEqual((la.band, la.frequency), ("L", "A"))
        self.assertEqual(la.polarizations, ("HH", "HV"))
        self.assertEqual(lb.polarizations, ("HH",))
        self.assertEqual(la.reference_date, "20260831")
        self.assertEqual(la.buffer_km, 5.0)
        self.assertEqual(la.source_manifest, self.project / "INPUT/NISAR/GSLC/source_manifest.json")
        self.assertEqual(la.kml_file, self.project / "INPUT/ROI/area.kml")
        self.assertEqual(la.output_dir, self.project / "WORK/NISAR/GSLC/LA/STACK")
        self.assertIsNone(la.kml_placemark)

    def test_rejects_invalid_numbers_science_choices_and_dates(self) -> None:
        invalid = (
            ("buffer_km = 5", "buffer_km = -1", "non-negative"),
            ("buffer_km = 5", "buffer_km = nan", "finite"),
            ("band = L", "band = X", "band"),
            ("frequency = A", "frequency = A,B", "frequency"),
            ("polarizations = HH, HV", "polarizations = HH, HH", "unique"),
            ("reference_date = 20260831", "reference_date = 2026-08-31", "YYYYMMDD"),
        )
        for original, changed, message in invalid:
            with self.subTest(changed=changed):
                self._write((original, changed))
                with self.assertRaisesRegex(ValueError, message):
                    load_nisar_gslc_stack_config(self.config)

    def test_requires_paths_within_project_and_separate_output(self) -> None:
        cases = (
            (
                "kml_file = INPUT/ROI/area.kml",
                "kml_file = ../../outside.kml",
                "outside project root",
            ),
            (
                "source_manifest = INPUT/NISAR/GSLC/source_manifest.json",
                "source_manifest = /tmp/outside.json",
                "outside project root",
            ),
            (
                "output_dir = WORK/NISAR/GSLC/LA/STACK",
                "output_dir = INPUT/NISAR/GSLC/output",
                "source product directory",
            ),
        )
        for original, changed, message in cases:
            with self.subTest(changed=changed):
                self._write((original, changed))
                with self.assertRaisesRegex(ValueError, message):
                    load_nisar_gslc_stack_config(self.config)

    def test_rejects_unknown_and_credential_fields(self) -> None:
        self._write(("buffer_km = 5", "buffer_km = 5\nmystery = value"))
        with self.assertRaisesRegex(ValueError, "unknown key"):
            load_nisar_gslc_stack_config(self.config)

        self._write(("buffer_km = 5", "buffer_km = 5\npassword = secret"))
        with self.assertRaisesRegex(ValueError, "credential"):
            load_nisar_gslc_stack_config(self.config)

    def test_requires_explicit_reference_and_distinct_case_names(self) -> None:
        self._write(("reference_date = 20260831\n", ""))
        with self.assertRaisesRegex(ValueError, "reference_date"):
            load_nisar_gslc_stack_config(self.config)

        self._write()
        with self.assertRaisesRegex(ValueError, "unknown.*missing"):
            select_nisar_gslc_stack_case(load_nisar_gslc_stack_config(self.config), "missing")


if __name__ == "__main__":
    unittest.main()
