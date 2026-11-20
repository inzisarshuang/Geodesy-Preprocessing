"""Tests for automatic GAMMA TOPS reference burst-table preparation."""

from __future__ import annotations

import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.tops.config import (
    GammaTopsSlcCase,
)


class GammaTopsBurstPreparationTests(unittest.TestCase):
    """Build a missing table and leave an existing table untouched."""

    def _case(self, root: Path) -> GammaTopsSlcCase:
        raw = root / "RAW"
        safe = raw / (
            "S1A_IW_SLC__1SSV_20231214T231201_20231214T231206_"
            "051657_063CE0_9970.SAFE"
        )
        annotation = safe / "annotation"
        annotation.mkdir(parents=True)
        (annotation / "s1a-iw2-slc-vv-reference.xml").write_text(
            "<product />\n", encoding="ascii"
        )
        roi = root / "ROI.kml"
        roi.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon>
<outerBoundaryIs><LinearRing><coordinates>
0.5,0.5 1.5,0.5 1.5,1.5 0.5,1.5 0.5,0.5
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>
""",
            encoding="utf-8",
        )
        return GammaTopsSlcCase(
            name="main",
            raw_dir=raw,
            slc_dir=root / "SLC",
            opod_dir=None,
            images_file=root / "images",
            burst_table=root / "AUX" / "ref_burst_number_table_135",
            range_looks=7,
            azimuth_looks=2,
            polarization="vv",
            workflow_flag=1,
            roi_file=roi,
            reference_selector="20231214",
            burst_coverage_plot=False,
        )

    @staticmethod
    def _module():
        try:
            return importlib.import_module(
                "geodesy_preprocessing.sar.slc_generation.gamma.tops.burst_prepare"
            )
        except ModuleNotFoundError as error:
            raise AssertionError("automatic burst preparation module is missing") from error

    def test_generates_missing_reference_table_from_safe_and_roi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self._case(Path(directory))

            def runner(command, **options):
                if Path(command[0]).name == "S1_burstloc":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=(
                            "Burst: reference.xml 1 135 IW2 VV 0 0 0 "
                            "0 0 0 2 2 2 2 0\n"
                        ),
                        stderr="",
                    )
                output_dir = Path(command[command.index("--out_dir") + 1])
                safe = Path(command[command.index("--dir_ref") + 1])
                (output_dir / f"{safe.stem}.burst_number_table").write_text(
                    "zipfile: ref.zip\n"
                    "iw2_number_of_bursts: 1\n"
                    "iw2_first_burst: 10.0\n"
                    "iw2_last_burst: 10.0\n",
                    encoding="ascii",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = self._module().ensure_reference_burst_table(
                case,
                runner=runner,
                burstloc_executable="S1_burstloc",
                helper_script="S1_BURST_tab_from_zipfile.py",
            )

            self.assertEqual(result, case.burst_table)
            self.assertIn(
                "iw2_number_of_bursts: 1",
                case.burst_table.read_text(encoding="ascii"),
            )
            self.assertEqual(
                (case.burst_table.parent / "burst_itab_135").read_text(
                    encoding="ascii"
                ),
                "2 1 1\n",
            )

    def test_existing_nonempty_table_is_reused_without_external_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self._case(Path(directory))
            case.burst_table.parent.mkdir(parents=True)
            case.burst_table.write_text("existing\n", encoding="ascii")

            def unexpected_runner(*_args, **_kwargs):
                self.fail("external command must not run for an existing table")

            result = self._module().ensure_reference_burst_table(
                case, runner=unexpected_runner
            )

            self.assertEqual(result, case.burst_table)
            self.assertEqual(case.burst_table.read_text(encoding="ascii"), "existing\n")

    def test_combines_swaths_and_burst_numbers_from_split_safe_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self._case(Path(directory))
            second_safe = case.raw_dir / (
                "S1A_IW_SLC__1SSV_20231214T231202_20231214T231206_"
                "051657_063CE0_PART2.SAFE"
            )
            annotation = second_safe / "annotation"
            annotation.mkdir(parents=True)
            for swath in (2, 3):
                (annotation / f"s1a-iw{swath}-slc-vv-part2.xml").write_text(
                    "<product />\n", encoding="ascii"
                )

            def runner(command, **options):
                if Path(command[0]).name == "S1_burstloc":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=(
                            f"Burst: {Path(command[1]).name} 1 135 IW2 VV 0 0 0 "
                            "0 0 0 2 2 2 2 0\n"
                        ),
                        stderr="",
                    )
                output_dir = Path(command[command.index("--out_dir") + 1])
                safe = Path(command[command.index("--dir_ref") + 1])
                is_second = "PART2" in safe.name
                rows = (
                    "iw2_number_of_bursts: 1\n"
                    f"iw2_first_burst: {11.0 if is_second else 10.0}\n"
                    f"iw2_last_burst: {11.0 if is_second else 10.0}\n"
                )
                if is_second:
                    rows += (
                        "iw3_number_of_bursts: 1\n"
                        "iw3_first_burst: 20.0\n"
                        "iw3_last_burst: 20.0\n"
                    )
                (output_dir / f"{safe.stem}.burst_number_table").write_text(
                    "zipfile: ref.zip\n" + rows, encoding="ascii"
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            self._module().ensure_reference_burst_table(
                case,
                runner=runner,
                burstloc_executable="S1_burstloc",
                helper_script="S1_BURST_tab_from_zipfile.py",
            )

            result = case.burst_table.read_text(encoding="ascii")
            self.assertIn("iw2_number_of_bursts: 2", result)
            self.assertIn("iw2_first_burst:      10.000000", result)
            self.assertIn("iw2_last_burst:       11.000000", result)
            self.assertIn("iw3_number_of_bursts: 1", result)
            self.assertIn("iw3_first_burst:      20.000000", result)


if __name__ == "__main__":
    unittest.main()
