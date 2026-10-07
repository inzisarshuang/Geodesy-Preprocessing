"""Tests for project-configured Sentinel-1 DEM geocoding."""

from __future__ import annotations

import importlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class GeoRefConfigTests(unittest.TestCase):
    """Resolve project cases and delegate them to the canonical CLI."""

    def _module(self, name: str):
        try:
            return importlib.import_module(
                f"geodesy_preprocessing.sar.rslc_registration.gamma.tops.{name}"
            )
        except ModuleNotFoundError:
            self.fail(f"{name} module has not been implemented")

    def _project(self, root: Path, *, complete: bool = True) -> Path:
        project = root / "study"
        dem = project / "DEM"
        dem.mkdir(parents=True)
        (dem / "srtm.dem").write_bytes(b"dem")
        (dem / "srtm.dem.par").write_text("width: 2\n", encoding="ascii")
        scene = project / "RAW_2024" / "SLC_154" / "20240905"
        scene.mkdir(parents=True)
        for suffix in ("vv.SLC_tab", "vv.iw1.slc", "vv.iw1.slc.par"):
            (scene / f"20240905.{suffix}").write_bytes(b"slc")
        output = project / "RAW_2024" / "DEM_geocode_154"
        if complete:
            output.mkdir()
            for suffix in (".lt_fine", ".utm.dem", ".utm.dem.par", ".mli.utm", ".hgt_sim"):
                (output / f"20240905{suffix}").write_bytes(b"complete")
        config_dir = project / "config" / "preprocessing"
        config_dir.mkdir(parents=True)
        config = config_dir / "gamma_sar_tops_geo_ref.cfg"
        config.write_text(
            "[project]\n"
            "root = ../..\n\n"
            "[defaults]\n"
            "range_looks = 7\n"
            "azimuth_looks = 2\n"
            "latitude_oversampling = auto\n"
            "longitude_oversampling = auto\n"
            "reference_flag = 1\n"
            "incidence_flag = 0\n\n"
            "dem_dir = DEM\n\n"
            "[case:2024_154]\n"
            "reference = 20240905\n"
            "slc_dir = RAW_2024/SLC_154\n"
            "output_dir = RAW_2024/DEM_geocode_154\n",
            encoding="utf-8",
        )
        return config

    def test_loads_defaults_and_builds_legacy_arguments(self) -> None:
        module = self._module("geo_ref_config")
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))

            cases = module.load_geo_ref_config(config)

            self.assertEqual(len(cases), 1)
            case = cases[0]
            self.assertEqual(case.name, "2024_154")
            self.assertEqual(case.reference, "20240905")
            self.assertEqual((case.range_looks, case.azimuth_looks), (7, 2))
            self.assertIsNone(case.latitude_oversampling)
            self.assertEqual(
                module.geo_ref_case_arguments(case),
                (
                    "20240905",
                    str(config.parents[2] / "DEM"),
                    str(config.parents[2] / "RAW_2024" / "SLC_154"),
                    str(config.parents[2] / "RAW_2024" / "DEM_geocode_154"),
                    "7",
                    "2",
                    "-",
                    "-",
                    "1",
                    "0",
                ),
            )

    def test_config_cli_uses_recorded_task_and_skips_complete_output(self) -> None:
        module = self._module("geo_ref_config_cli")
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))
            terminal = io.StringIO()
            environment = {
                "GEODESY_PROGRESS_PROTOCOL": "1",
                "GEODESY_TASK": "PRE_S1_GEO_REF",
            }

            with patch.dict(os.environ, environment), redirect_stdout(terminal):
                exit_code = module.main(
                    ["--config", str(config), "--case", "2024_154"]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn('"status":"skipped"', terminal.getvalue())
            self.assertIn('"task":"PRE_S1_GEO_REF"', terminal.getvalue())

    def test_unknown_keys_and_partial_auto_oversampling_are_rejected(self) -> None:
        module = self._module("geo_ref_config")
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))
            text = config.read_text(encoding="utf-8")
            config.write_text(text.replace("reference_flag = 1", "mystery = 1"))
            with self.assertRaisesRegex(ValueError, "unknown key"):
                module.load_geo_ref_config(config)

            config.write_text(
                text.replace("longitude_oversampling = auto", "longitude_oversampling = 1.5")
            )
            with self.assertRaisesRegex(ValueError, "both be auto"):
                module.load_geo_ref_config(config)


if __name__ == "__main__":
    unittest.main()
