"""Installed-wheel acceptance for the stage-first preprocessing layout."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstalledArchitectureV2Tests(unittest.TestCase):
    """Prove the wheel works without importing from the source checkout."""

    def test_version_is_accepted_release(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            metadata = tomllib.load(stream)
        self.assertEqual(metadata["project"]["version"], "0.8.0")

    def test_wheel_contains_new_packages_commands_and_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            wheel_dir = temporary / "wheel"
            wheel_dir.mkdir()
            subprocess.run(
                (
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheel_dir),
                    str(ROOT),
                ),
                check=True,
                cwd=temporary,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wheel = next(wheel_dir.glob("geodesy_preprocessing-0.8.0-*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
                for suffix in (
                    "sar/acquisition/nisar/cli.py",
                    "sar/acquisition/nisar/download.py",
                    "sar/acquisition/sentinel1/cli.py",
                    "sar/acquisition/sentinel1/download.py",
                    "sar/acquisition/sentinel1/merge.py",
                    "sar/gslc_subsetting/nisar/cli.py",
                    "sar/gslc_subsetting/nisar/runner.py",
                    "sar/slc_generation/gamma/radar_grid/adapters/nisar.py",
                    "sar/common/frequency_bundle.py",
                    "sar/rslc_registration/common/result.py",
                    "sar/rslc_registration/gamma/tops/coreg_runner.py",
                    "sar/rslc_registration/gamma/radar_grid/coreg_runner.py",
                    "sar/slc_generation/gamma/tops/runner.py",
                    "sar/slc_generation/gamma/radar_grid/runner.py",
                    "sar/rslc_subsetting/gamma/radar_grid/cli.py",
                    "share/geodesy-preprocessing/config_examples/preprocessing/gamma_coreg_tops.cfg",
                    "share/geodesy-preprocessing/config_examples/preprocessing/gamma_geo_ref_grid.cfg",
                    "share/geodesy-preprocessing/config_examples/preprocessing/gamma_coreg_grid.cfg",
                    "share/geodesy-preprocessing/config_examples/preprocessing/gamma_rslc_crop.cfg",
                    "share/geodesy-preprocessing/config_examples/acquisition/nisar_rslc_download.cfg",
                    "share/geodesy-preprocessing/config_examples/acquisition/nisar_gslc_download.cfg",
                    "share/geodesy-preprocessing/config_examples/preprocessing/nisar_gslc_stack.cfg",
                    "share/geodesy-preprocessing/config_examples/acquisition/sentinel1_download.cfg",
                ):
                    self.assertTrue(any(name.endswith(suffix) for name in names), suffix)
                removed_templates = {
                    "sentinel1_slc.cfg",
                    "sentinel1_geo_ref.cfg",
                    "sentinel1_coreg.cfg",
                    "other_sar_slc.cfg",
                }
                self.assertTrue(
                    all(not any(name.endswith(template) for name in names) for template in removed_templates)
                )
                for removed_package in (
                    "/sar/registration/",
                    "/sar/subsetting/",
                    "/sar/preparation/",
                ):
                    self.assertFalse(any(removed_package in name for name in names), removed_package)

            environment = temporary / "environment"
            venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
            python = environment / "bin" / "python"
            subprocess.run(
                (
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--ignore-installed",
                    str(wheel),
                ),
                check=True,
                cwd=temporary,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            scripts = (
                "geodesy-pre-dem",
                "geodesy-pre-gamma-dem-convert",
                "geodesy-pre-gamma-slc-grid",
                "geodesy-pre-gamma-slc-tops",
                "geodesy-pre-gamma-geo-ref-tops",
                "geodesy-pre-gamma-coreg-tops",
                "geodesy-pre-gamma-geo-ref-grid",
                "geodesy-pre-gamma-coreg-grid",
                "geodesy-pre-gamma-rslc-crop",
                "geodesy-pre-sar-nisar-acquire",
                "geodesy-pre-sar-nisar-gslc",
                "geodesy-pre-sar-sentinel1-acquire",
            )
            clean_environment = os.environ.copy()
            clean_environment.pop("PYTHONPATH", None)
            for script in scripts:
                completed = subprocess.run(
                    (str(environment / "bin" / script), "--help"),
                    cwd=temporary,
                    env=clean_environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout)
            removed = subprocess.run(
                (
                    str(python),
                    "-c",
                    "import importlib.util; "
                    "assert importlib.util.find_spec('geodesy_preprocessing.sentinel1') is None; "
                    "assert importlib.util.find_spec('geodesy_preprocessing.other_sar') is None",
                ),
                cwd=temporary,
                env=clean_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            self.assertEqual(removed.returncode, 0, removed.stdout)


if __name__ == "__main__":
    unittest.main()
