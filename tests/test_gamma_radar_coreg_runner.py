"""Tests for standard GAMMA radar-grid registration and atomic publication."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from geodesy_preprocessing.sar.common.stack_manifest import read_stack_manifest
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_config import (
    GammaRadarCoregCase,
    GammaRadarQualityThresholds,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_plan import (
    build_gamma_radar_coreg_plan,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_runner import (
    GammaRadarCoregCommandError,
    run_gamma_radar_coreg,
)


PARAMETERS = """range_samples: 100
azimuth_lines: 80
range_pixel_spacing: 2.0 m
azimuth_pixel_spacing: 3.0 m
center_frequency: 1.2575e9 Hz
"""
QUALITY = """SLC coregistration quality file
final solution: 100 offset estimates accepted out of 120 samples
final range offset poly. coeff.: -0.04 0.0
final azimuth offset poly. coeff.: -0.03 0.0
final model fit std. dev. (samples) range: 0.02 azimuth: 0.03
end of SLC_coreg.py
"""


class GammaRadarCoregRunnerTests(unittest.TestCase):
    """Keep inputs read-only and publish only a complete quality-approved stack."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.trace = self.root / "trace.jsonl"
        self.slc = self.root / "SLC"
        self.input_files: list[Path] = []
        for date in ("20240229", "20240312"):
            scene = self.slc / "HH_SLC" / date
            scene.mkdir(parents=True)
            data = scene / f"{date}.slc"
            parameter = scene / f"{date}.slc.par"
            data.write_bytes(date.encode())
            parameter.write_text(PARAMETERS, encoding="ascii")
            self.input_files.extend((data, parameter))
        self.images = self.root / "images"
        self.images.write_text("20240229\n20240312\n", encoding="ascii")
        geometry = self.root / "GEOMETRY"
        geometry.mkdir()
        self.height = geometry / "20240229.HH.wgs84.hgt"
        self.height.write_bytes(b"height")
        self.geometry_manifest = geometry / "reference_geometry.json"
        self.geometry_manifest.write_text(
            json.dumps(
                {
                    "reference_date": "20240229",
                    "primary_polarization": "HH",
                    "outputs": [self.height.name],
                }
            )
        )
        self.output = self.root / "RSLC"
        self._commands()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(
        self,
        *,
        selected_polarizations: tuple[str, ...] = ("HH",),
        refinement: str = "standard",
    ) -> GammaRadarCoregCase:
        return GammaRadarCoregCase(
            "main",
            "nisar",
            "A",
            "20240229",
            "HH",
            selected_polarizations,
            self.slc,
            self.images,
            self.geometry_manifest,
            self.output,
            5,
            3,
            refinement,  # type: ignore[arg-type]
            1,
            GammaRadarQualityThresholds(0.10, 0.30, 0.20, 0.50),
            track="87",
            frame="22",
            orbit_direction="DESCENDING",
        )

    def _write_command(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def _commands(
        self,
        *,
        exit_code: int = 0,
        quality: str = QUALITY,
        refine_exit: int = 0,
        refined_quality: str = QUALITY,
    ) -> None:
        self._write_command(
            "multi_look",
            """import json, os, pathlib, sys
with open(os.environ['TRACE_FILE'], 'a') as stream: stream.write(json.dumps(sys.argv) + '\\n')
pathlib.Path(sys.argv[3]).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(sys.argv[3]).write_bytes(b'rmli')
pathlib.Path(sys.argv[4]).write_text('range_samples: 20\\n')
""",
        )
        self._write_command(
            "SLC_coreg.py",
            f"""import json, os, pathlib, sys
with open(os.environ['TRACE_FILE'], 'a') as stream: stream.write(json.dumps(sys.argv) + '\\n')
if {exit_code}: raise SystemExit({exit_code})
for index in (3, 4, 5, 6):
    path = pathlib.Path(sys.argv[index]); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'product')
pathlib.Path(sys.argv[1] + '.lt_fine').write_bytes(b'lt')
pathlib.Path(sys.argv[1] + '.off').write_bytes(b'off')
pathlib.Path(sys.argv[3] + '.coreg_quality').write_text({quality!r})
""",
        )
        self._write_command(
            "SLC_interp_lt",
            """import json, os, pathlib, sys
with open(os.environ['TRACE_FILE'], 'a') as stream: stream.write(json.dumps(sys.argv) + '\\n')
for index in (8, 9):
    path = pathlib.Path(sys.argv[index]); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'interpolated')
""",
        )
        self._write_command(
            "SLC_coreg_refine.py",
            f"""import json, os, pathlib, sys
with open(os.environ['TRACE_FILE'], 'a') as stream: stream.write(json.dumps(sys.argv) + '\\n')
if {refine_exit}: raise SystemExit({refine_exit})
pathlib.Path(sys.argv[sys.argv.index('--quality') + 1]).write_text({refined_quality!r})
""",
        )

    def _add_hv(self) -> None:
        for date in ("20240229", "20240312"):
            scene = self.slc / "HV_SLC" / date
            scene.mkdir(parents=True)
            data = scene / f"{date}.slc"
            parameter = scene / f"{date}.slc.par"
            data.write_bytes(f"{date}-HV".encode())
            parameter.write_text(PARAMETERS, encoding="ascii")
            self.input_files.extend((data, parameter))

    def _environment(self) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "TRACE_FILE": str(self.trace),
            "GAMMA_VERSION": "2025.2",
        }

    def test_registers_reference_and_secondary_then_publishes_manifest(self) -> None:
        before = {path: path.read_bytes() for path in self.input_files}
        events: list[str] = []
        result = run_gamma_radar_coreg(
            build_gamma_radar_coreg_plan(self._case()),
            environment=self._environment(),
            progress_sink=events.append,
        )
        self.assertEqual(result.reference_date, "20240229")
        manifest = read_stack_manifest(self.output / "registered_stack.json")
        self.assertEqual(manifest.frequencies, ("1257500000",))
        self.assertEqual(manifest.geometry_type.value, "radar_grid")
        self.assertEqual(
            (manifest.track, manifest.frame, manifest.orbit_direction),
            ("87", "22", "DESCENDING"),
        )
        self.assertEqual(len(manifest.assets), 2)
        self.assertTrue((self.output / "quality.json").is_file())
        self.assertTrue((self.output / "provenance.json").is_file())
        self.assertEqual((self.output / "images").read_text(), "20240229\n20240312\n")
        commands = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual([Path(command[0]).name for command in commands], ["multi_look", "SLC_coreg.py"])
        self.assertIn("--sar_mode", commands[1])
        self.assertEqual(before, {path: path.read_bytes() for path in self.input_files})
        for path in self.input_files:
            self.assertFalse(Path(str(path) + ".lt_fine").exists())
            self.assertFalse(Path(str(path) + ".off").exists())
        self.assertTrue(events)

    def test_command_failure_and_unparseable_quality_block_publication(self) -> None:
        self._commands(exit_code=7)
        with self.assertRaises(GammaRadarCoregCommandError) as raised:
            run_gamma_radar_coreg(
                build_gamma_radar_coreg_plan(self._case()), environment=self._environment()
            )
        self.assertTrue(raised.exception.stderr_log.is_file())
        self.assertFalse(self.output.exists())
        self._commands(quality="broken")
        with self.assertRaisesRegex(ValueError, "quality"):
            run_gamma_radar_coreg(
                build_gamma_radar_coreg_plan(self._case()), environment=self._environment()
            )
        self.assertFalse(self.output.exists())

    def test_hard_failure_blocks_but_warning_scene_publishes(self) -> None:
        self._commands(quality=QUALITY.replace("range: 0.02", "range: 0.40"))
        with self.assertRaisesRegex(ValueError, "hard quality"):
            run_gamma_radar_coreg(
                build_gamma_radar_coreg_plan(self._case()), environment=self._environment()
            )
        self.assertFalse(self.output.exists())
        self._commands(quality=QUALITY.replace("range: 0.02", "range: 0.15"))
        run_gamma_radar_coreg(
            build_gamma_radar_coreg_plan(self._case()), environment=self._environment()
        )
        quality = json.loads((self.output / "quality.json").read_text())
        self.assertEqual(quality["scenes"]["20240312/HH"]["status"], "warning")

    def test_partial_destination_is_rejected_before_command_and_complete_skips(self) -> None:
        self.output.mkdir()
        (self.output / "partial").write_bytes(b"x")
        with self.assertRaisesRegex(FileExistsError, "incomplete RSLC destination"):
            run_gamma_radar_coreg(
                build_gamma_radar_coreg_plan(self._case()), environment=self._environment()
            )
        self.assertFalse(self.trace.exists())

        for child in self.output.iterdir():
            child.unlink()
        self.output.rmdir()
        first = run_gamma_radar_coreg(
            build_gamma_radar_coreg_plan(self._case()), environment=self._environment()
        )
        trace = self.trace.read_bytes()
        second = run_gamma_radar_coreg(
            build_gamma_radar_coreg_plan(self._case()), environment=self._environment()
        )
        self.assertEqual((first.native_root, second.native_root), (self.output, self.output))
        self.assertEqual(trace, self.trace.read_bytes())

        with self.assertRaisesRegex(FileExistsError, "different inputs"):
            run_gamma_radar_coreg(
                build_gamma_radar_coreg_plan(
                    replace(self._case(), offset_range_window=512)
                ),
                environment=self._environment(),
            )

        self.input_files[0].write_bytes(b"changed-input")
        with self.assertRaisesRegex(FileExistsError, "different inputs"):
            run_gamma_radar_coreg(
                build_gamma_radar_coreg_plan(self._case()),
                environment=self._environment(),
            )

    def test_secondary_polarization_reuses_primary_lookup_and_offset(self) -> None:
        self._add_hv()
        run_gamma_radar_coreg(
            build_gamma_radar_coreg_plan(
                self._case(selected_polarizations=("HH", "HV"))
            ),
            environment=self._environment(),
        )
        commands = [json.loads(line) for line in self.trace.read_text().splitlines()]
        names = [Path(command[0]).name for command in commands]
        self.assertEqual(names.count("SLC_coreg.py"), 1)
        self.assertEqual(names.count("SLC_interp_lt"), 1)
        interpolation = next(command for command in commands if Path(command[0]).name == "SLC_interp_lt")
        self.assertIn("HH_RSLC/20240312/20240312.lt_fine", interpolation[4])
        self.assertIn("HH_RSLC/20240312/20240312.off", interpolation[7])
        quality = json.loads((self.output / "quality.json").read_text())
        self.assertEqual(
            quality["scenes"]["20240312/HV"]["geometry_reused_from"],
            "20240312/HH",
        )

    def test_refinement_quality_replaces_standard_and_failure_blocks_publication(self) -> None:
        refined = QUALITY.replace("range: 0.02", "range: 0.15").replace(
            "end of SLC_coreg.py", "end of MLI_coreg_refine.py"
        )
        self._commands(refined_quality=refined)
        run_gamma_radar_coreg(
            build_gamma_radar_coreg_plan(self._case(refinement="offset_field")),
            environment=self._environment(),
        )
        commands = [json.loads(line) for line in self.trace.read_text().splitlines()]
        names = [Path(command[0]).name for command in commands]
        self.assertEqual(names, ["multi_look", "SLC_coreg.py", "SLC_coreg_refine.py"])
        quality = json.loads((self.output / "quality.json").read_text())
        self.assertEqual(quality["scenes"]["20240312/HH"]["status"], "warning")

        # A fresh destination proves refinement failure prevents publication.
        self.output = self.root / "RSLC_FAILED"
        self.trace.unlink()
        self._commands(refine_exit=9)
        with self.assertRaises(GammaRadarCoregCommandError):
            run_gamma_radar_coreg(
                build_gamma_radar_coreg_plan(self._case(refinement="offset_field")),
                environment=self._environment(),
            )
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
