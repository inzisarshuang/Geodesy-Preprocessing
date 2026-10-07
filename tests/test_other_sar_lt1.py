"""Behavior tests for the LT-1 vendor adapter."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from fixtures.other_sar import write_lt1_product
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.adapters.lt1 import Lt1Adapter
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import OtherSarSlcCase


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Lt1AdapterTests(unittest.TestCase):
    """Pin structural pairing, isolated orbit filtering, and legacy outputs."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "RAW"
        self.raw.mkdir()
        self.slc = self.root / "SLC"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(self, commands: dict[str, str] | None = None) -> OtherSarSlcCase:
        return OtherSarSlcCase(
            name="main",
            sensor="lt1",
            raw_dir=self.raw,
            slc_dir=self.slc,
            images_file=self.root / "images_lt1",
            polarizations=("HH",),
            range_looks=10,
            azimuth_looks=20,
            commands=MappingProxyType(commands or {}),
            workflow_task="PRE_OTHER_SAR_SLC",
        )

    def test_builds_exact_order_degree_and_flat_output_group(self) -> None:
        image, metadata = write_lt1_product(self.raw, date="20260922")
        adapter = Lt1Adapter()
        case = self._case()
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        prepared = adapter.prepare(product, stage)
        plan = adapter.build_scene_plan(case, prepared)
        work_par = stage / "work/20260922.slc.par"
        original = stage / "products/20260922.slc.par.orig"
        orbit_input = stage / "work/20260922.orbit-input.slc.par"
        filtered = stage / "products/20260922.slc.par"
        slc = stage / "products/20260922.slc"

        self.assertEqual(
            tuple(command.stage for command in plan.commands),
            ("import_hh", "preserve_original", "isolate_orbit_input", "orbit_filter", "preview"),
        )
        self.assertEqual(
            plan.commands[0].argv,
            ("par_LT1_SLC", str(image), str(metadata), str(work_par), str(slc), "1"),
        )
        self.assertEqual(plan.commands[1].argv, ("cp", "--", str(work_par), str(original)))
        self.assertEqual(plan.commands[2].argv, ("cp", "--", str(work_par), str(orbit_input)))
        self.assertEqual(
            plan.commands[3].argv,
            (
                "ORB_filt_spline.py",
                str(orbit_input),
                str(filtered),
                "--png",
                str(stage / "orbit_png"),
                "--degree",
                "5",
            ),
        )
        self.assertEqual(plan.commands[4].argv[:3], ("rasSLC", str(slc), "{range_samples}"))
        self.assertEqual(plan.commands[4].parameter_file, filtered)
        self.assertEqual(
            tuple(output.destination for output in plan.outputs),
            (
                self.slc / "20260922.slc",
                self.slc / "20260922.slc.par",
                self.slc / "20260922.slc.par.orig",
                self.slc / "20260922.slc.bmp",
            ),
        )

    def test_command_overrides_are_split_without_a_shell(self) -> None:
        write_lt1_product(self.raw, date="20260922")
        case = self._case(
            {
                "import_command": "/opt/vendor/par_LT1_SLC --strict",
                "orbit_filter_command": "/opt/tools/orbit-filter --safe",
                "preview_command": "/opt/gamma/rasSLC",
            }
        )
        adapter = Lt1Adapter()
        product = adapter.discover(case)[0]
        stage = self.root / ".stage"
        stage.mkdir()
        plan = adapter.build_scene_plan(case, adapter.prepare(product, stage))
        self.assertEqual(plan.commands[0].argv[:2], ("/opt/vendor/par_LT1_SLC", "--strict"))
        self.assertEqual(plan.commands[3].argv[:2], ("/opt/tools/orbit-filter", "--safe"))
        self.assertEqual(plan.commands[4].argv[0], "/opt/gamma/rasSLC")

    def test_mismatched_or_ambiguous_pairs_and_duplicate_dates_are_rejected(self) -> None:
        image, metadata = write_lt1_product(self.raw, date="20260922")
        metadata.rename(metadata.with_name("DIFFERENT_20260922.meta.xml"))
        with self.assertRaisesRegex(ValueError, "matching TIFF and metadata"):
            Lt1Adapter().discover(self._case())

        shutil.rmtree(image.parent)
        image, metadata = write_lt1_product(self.raw, date="20260922")
        (image.parent / "SECOND_20260922.tiff").write_bytes(b"second")
        with self.assertRaisesRegex(ValueError, "exactly one TIFF"):
            Lt1Adapter().discover(self._case())

        shutil.rmtree(image.parent)
        write_lt1_product(self.raw, date="20260922", scene_name="scene-a")
        write_lt1_product(self.raw, date="20260922", scene_name="scene-b")
        with self.assertRaisesRegex(ValueError, "duplicate acquisition date"):
            Lt1Adapter().discover(self._case())

    def test_orbit_filter_cannot_modify_vendor_or_published_original_parameter(self) -> None:
        image, metadata = write_lt1_product(self.raw, date="20260922")
        fake = self.root / "fake_orbit.py"
        fake.write_text(
            """from pathlib import Path
import sys
source = Path(sys.argv[1])
destination = Path(sys.argv[2])
source.write_text('mutated orbit input')
destination.write_text('range_samples: 4\\nfiltered')
""",
            encoding="utf-8",
        )
        case = self._case({"orbit_filter_command": f"{sys.executable} {fake}"})
        adapter = Lt1Adapter()
        product = adapter.discover(case)[0]
        source_before = (_digest(image), _digest(metadata))
        stage = self.root / ".stage"
        stage.mkdir()
        plan = adapter.build_scene_plan(case, adapter.prepare(product, stage))
        work_par = stage / "work/20260922.slc.par"
        work_par.parent.mkdir(parents=True, exist_ok=True)
        work_par.write_text("range_samples: 4\noriginal")

        subprocess.run(plan.commands[1].argv, check=True)
        subprocess.run(plan.commands[2].argv, check=True)
        subprocess.run(plan.commands[3].argv, check=True)

        self.assertEqual((_digest(image), _digest(metadata)), source_before)
        self.assertEqual(
            (stage / "products/20260922.slc.par.orig").read_text(),
            "range_samples: 4\noriginal",
        )
        self.assertEqual(
            (stage / "work/20260922.orbit-input.slc.par").read_text(),
            "mutated orbit input",
        )
        self.assertIn("filtered", (stage / "products/20260922.slc.par").read_text())


if __name__ == "__main__":
    unittest.main()
