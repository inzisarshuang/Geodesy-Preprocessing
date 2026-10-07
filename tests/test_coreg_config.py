"""Tests for project-configured Sentinel-1 TOPS coregistration."""

from __future__ import annotations

import importlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class CoregConfigTests(unittest.TestCase):
    """Resolve strict project cases and execute the canonical coreg CLI."""

    def _module(self, name: str):
        try:
            return importlib.import_module(
                f"geodesy_preprocessing.sar.rslc_registration.gamma.tops.{name}"
            )
        except ModuleNotFoundError:
            self.fail(f"{name} module has not been implemented")

    def _project(self, root: Path) -> Path:
        project = root / "study"
        slc = project / "RAW_2024" / "SLC_154"
        for date in ("20240905", "20240917"):
            scene = slc / date
            scene.mkdir(parents=True)
            names = (
                f"{date}.vv.iw1.slc",
                f"{date}.vv.iw1.slc.par",
                f"{date}.vv.iw1.slc.tops_par",
            )
            for name in names:
                (scene / name).write_bytes(b"slc")
            (scene / f"{date}.vv.SLC_tab").write_text(
                " ".join(names) + "\n", encoding="utf-8"
            )
        raw = project / "RAW_2024"
        (raw / "images_154").write_text("20240905\n20240917\n", encoding="ascii")
        dem = raw / "DEM_geocode_154"
        dem.mkdir()
        (dem / "20240905.hgt_sim").write_bytes(b"height")
        output = raw / "RSLC_154"
        (output / "RSLC").mkdir(parents=True)
        (output / "coreg_quality").mkdir()
        for date in ("20240905", "20240917"):
            (output / "RSLC" / f"{date}.rslc").write_bytes(b"rslc")
            (output / "RSLC" / f"{date}.rslc.par").write_bytes(b"par")
        (output / "coreg_quality" / "1ID.coreg_quality").write_bytes(b"quality")
        config_dir = project / "config" / "preprocessing"
        config_dir.mkdir(parents=True)
        config = config_dir / "gamma_sar_tops_coreg.cfg"
        config.write_text(
            "[project]\n"
            "root = ../..\n\n"
            "[defaults]\n"
            "range_looks = 7\n"
            "azimuth_looks = 2\n"
            "coherence_threshold = 0.8\n"
            "fraction_threshold = 0.01\n"
            "phase_stdev_threshold = 0.8\n"
            "id_mode = date\n"
            "npoly = 4\n"
            "bridge_mode = none\n\n"
            "[case:2024_154]\n"
            "slc_dir = RAW_2024/SLC_154\n"
            "dem_geocode_dir = RAW_2024/DEM_geocode_154\n"
            "output_dir = RAW_2024/RSLC_154\n"
            "reference = 20240905\n"
            "images = RAW_2024/images_154\n",
            encoding="utf-8",
        )
        return config

    def test_loads_settings_and_builds_cli_arguments(self) -> None:
        module = self._module("coreg_config")
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))

            case = module.load_coreg_config(config)[0]

            self.assertEqual(case.name, "2024_154")
            self.assertEqual(case.reference, "20240905")
            self.assertEqual((case.range_looks, case.azimuth_looks), (7, 2))
            self.assertEqual(case.npoly, 4)
            arguments = module.coreg_case_arguments(case)
            self.assertEqual(arguments[:7], (
                str(config.parents[2] / "RAW_2024" / "SLC_154"),
                str(config.parents[2] / "RAW_2024" / "DEM_geocode_154"),
                str(config.parents[2] / "RAW_2024" / "RSLC_154"),
                "20240905",
                str(config.parents[2] / "RAW_2024" / "images_154"),
                "7",
                "2",
            ))
            self.assertIn("--bridge-mode", arguments)
            self.assertIn("none", arguments)

    def test_config_cli_reports_every_existing_scene_as_skipped(self) -> None:
        module = self._module("coreg_config_cli")
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))
            terminal = io.StringIO()
            environment = {
                "GEODESY_PROGRESS_PROTOCOL": "1",
                "GEODESY_TASK": "PRE_S1_COREG",
            }

            with patch.dict(os.environ, environment), redirect_stdout(terminal):
                exit_code = module.main(["--config", str(config), "--case", "2024_154"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(terminal.getvalue().count('"status":"skipped"'), 2)
            self.assertIn('"task":"PRE_S1_COREG"', terminal.getvalue())

    def test_unknown_key_and_invalid_enum_are_rejected(self) -> None:
        module = self._module("coreg_config")
        with tempfile.TemporaryDirectory() as directory:
            config = self._project(Path(directory))
            text = config.read_text(encoding="utf-8")
            config.write_text(text.replace("npoly = 4", "mystery = 4"))
            with self.assertRaisesRegex(ValueError, "unknown key"):
                module.load_coreg_config(config)

            config.write_text(text.replace("bridge_mode = none", "bridge_mode = other"))
            with self.assertRaisesRegex(ValueError, "bridge mode"):
                module.load_coreg_config(config)


if __name__ == "__main__":
    unittest.main()
