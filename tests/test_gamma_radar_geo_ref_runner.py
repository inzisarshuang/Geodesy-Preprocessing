"""Tests for transactional GAMMA radar-grid reference-geometry execution."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_config import (
    GammaRadarGeoRefCase,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_plan import (
    build_gamma_radar_geo_ref_plan,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_runner import (
    GammaRadarGeoRefCommandError,
    run_gamma_radar_geo_ref,
)


PARAMETERS = """range_samples: 100
azimuth_lines: 80
range_pixel_spacing: 2.0 m
azimuth_pixel_spacing: 3.0 m
"""


class GammaRadarGeoRefRunnerTests(unittest.TestCase):
    """Require ordered commands, complete staged outputs, and atomic publication."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.trace = self.root / "trace.jsonl"
        self.slc = self.root / "SLC/HH_SLC/20240229"
        self.slc.mkdir(parents=True)
        self.slc_data = self.slc / "20240229.slc"
        self.slc_data.write_bytes(b"immutable-slc")
        self.slc_par = self.slc / "20240229.slc.par"
        self.slc_par.write_text(PARAMETERS, encoding="ascii")
        self.images = self.root / "images"
        self.images.write_text("20240229\n", encoding="ascii")
        self.dem = self.root / "DEM/srtm.dem"
        self.dem.parent.mkdir()
        self.dem.write_bytes(b"immutable-dem")
        self.dem_par = self.root / "DEM/srtm.dem.par"
        self.dem_par.write_text("width: 10\nnlines: 20\n", encoding="ascii")
        self.output = self.root / "GEOMETRY"
        self._commands()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(self) -> GammaRadarGeoRefCase:
        return GammaRadarGeoRefCase(
            name="main",
            reference_date="20240229",
            primary_polarization="HH",
            slc_dir=self.root / "SLC",
            images_file=self.images,
            dem=self.dem,
            dem_par=self.dem_par,
            output_dir=self.output,
            range_looks=5,
            azimuth_looks=3,
            latitude_oversampling=None,
            longitude_oversampling=None,
            post_spacing=20.0,
            correlation_threshold=0.2,
            polynomial_parameters=1,
            range_offset_estimates=None,
            azimuth_offset_estimates=None,
            range_window=128,
            azimuth_window=128,
            area_factor=1.0,
        )

    def _write_command(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def _commands(self, *, geocoding_exit: int = 0, include_height: bool = True) -> None:
        self._write_command(
            "multi_look",
            """import json, os, pathlib, sys
with open(os.environ['TRACE_FILE'], 'a') as stream:
    stream.write(json.dumps(sys.argv) + '\\n')
pathlib.Path(sys.argv[3]).write_bytes(b'mli')
pathlib.Path(sys.argv[4]).write_text('range_samples: 20\\n')
""",
        )
        height_line = "(out / f'{root}.wgs84.hgt').write_bytes(b'hgt')" if include_height else ""
        self._write_command(
            "geocoding.py",
            f"""import json, os, pathlib, sys
with open(os.environ['TRACE_FILE'], 'a') as stream:
    stream.write(json.dumps(sys.argv) + '\\n')
if {geocoding_exit}:
    raise SystemExit({geocoding_exit})
out = pathlib.Path(sys.argv[sys.argv.index('--out_dir') + 1])
root = sys.argv[5]
demroot = pathlib.Path(sys.argv[4]).name.split('.')[0]
{height_line}
for suffix in ('lt_fine', 'inc', 'ls_map'):
    (out / f'{{demroot}}.{{root}}.{{suffix}}').write_bytes(b'geo')
(out / f'{{root}}.ls_map_rdc').write_bytes(b'geo')
(out / f'{{demroot}}.{{root}}.mli').write_bytes(b'geo')
summary = pathlib.Path(sys.argv[sys.argv.index('--summary') + 1])
summary.write_text('quality ok\\n')
""",
        )

    def _environment(self) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "TRACE_FILE": str(self.trace),
            "GAMMA_VERSION": "2024-10-17",
        }

    def test_runs_multi_look_then_geocoding_and_publishes_manifest(self) -> None:
        before = (self.slc_data.read_bytes(), self.slc_par.read_bytes(), self.dem.read_bytes())
        events: list[str] = []
        result = run_gamma_radar_geo_ref(
            build_gamma_radar_geo_ref_plan(self._case()),
            environment=self._environment(),
            progress_sink=events.append,
        )
        self.assertEqual(result.status, "complete")
        commands = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual([Path(command[0]).name for command in commands], ["multi_look", "geocoding.py"])
        out_index = commands[1].index("--out_dir") + 1
        self.assertNotEqual(Path(commands[1][out_index]), self.output)
        self.assertIn(".stage", Path(commands[1][out_index]).name)
        self.assertTrue((self.output / "20240229.HH.wgs84.hgt").is_file())
        manifest = json.loads((self.output / "reference_geometry.json").read_text())
        self.assertEqual(manifest["reference_date"], "20240229")
        self.assertEqual(manifest["primary_polarization"], "HH")
        self.assertEqual(manifest["looks"], {"azimuth": 3, "range": 5})
        self.assertEqual(manifest["gamma_version"], "2024-10-17")
        self.assertEqual(len(manifest["commands"]), 2)
        self.assertIn("sha256", manifest["inputs"]["slc"])
        self.assertEqual(before, (self.slc_data.read_bytes(), self.slc_par.read_bytes(), self.dem.read_bytes()))
        self.assertTrue(events)

    def test_missing_wgs84_height_blocks_publication(self) -> None:
        self._commands(include_height=False)
        with self.assertRaisesRegex(ValueError, "WGS84 radar height"):
            run_gamma_radar_geo_ref(
                build_gamma_radar_geo_ref_plan(self._case()),
                environment=self._environment(),
            )
        self.assertFalse(self.output.exists())

    def test_command_failure_blocks_publication(self) -> None:
        self._commands(geocoding_exit=7)
        with self.assertRaises(GammaRadarGeoRefCommandError) as raised:
            run_gamma_radar_geo_ref(
                build_gamma_radar_geo_ref_plan(self._case()),
                environment=self._environment(),
            )
        self.assertEqual(raised.exception.exit_code, 7)
        self.assertTrue(raised.exception.stderr_log.is_file())
        self.assertFalse(self.output.exists())

    def test_complete_identical_output_skips_without_commands(self) -> None:
        first = run_gamma_radar_geo_ref(
            build_gamma_radar_geo_ref_plan(self._case()),
            environment=self._environment(),
        )
        trace_before = self.trace.read_bytes()
        second = run_gamma_radar_geo_ref(
            build_gamma_radar_geo_ref_plan(self._case()),
            environment=self._environment(),
        )
        self.assertEqual((first.status, second.status), ("complete", "skipped"))
        self.assertEqual(self.trace.read_bytes(), trace_before)

    def test_complete_output_rejects_changed_inputs(self) -> None:
        run_gamma_radar_geo_ref(
            build_gamma_radar_geo_ref_plan(self._case()),
            environment=self._environment(),
        )
        self.dem.write_bytes(b"changed-dem")
        with self.assertRaisesRegex(FileExistsError, "different inputs"):
            run_gamma_radar_geo_ref(
                build_gamma_radar_geo_ref_plan(self._case()),
                environment=self._environment(),
            )


if __name__ == "__main__":
    unittest.main()
