"""Tests for the backend-neutral Sentinel-1 acquisition-to-GAMMA handoff."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.acquisition.sentinel1.config import (
    load_sentinel1_acquisition_config,
)
from geodesy_preprocessing.sar.slc_generation.gamma.tops.config import load_slc_config


ROOT = Path(__file__).resolve().parents[1]


class Sentinel1AcquisitionWorkflowContractTests(unittest.TestCase):
    def test_acquisition_modules_do_not_import_gamma_backend(self) -> None:
        directory = ROOT / "src/geodesy_preprocessing/sar/acquisition/sentinel1"
        violations = []
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(".gamma" in name or name.endswith("gamma") for name in names):
                    violations.append(f"{path.name}: {names}")
        self.assertEqual(violations, [])

    def test_query_and_download_paths_feed_tops_slc_without_path_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            config_dir = project / "config/preprocessing"
            config_dir.mkdir(parents=True)
            (project / "AOI").mkdir()
            (project / "AOI/a.kml").write_text("<kml/>")
            acquisition = config_dir / "sentinel1_download.cfg"
            acquisition.write_text("""
[project]
root = ../..
[case:main]
kml_file = AOI/a.kml
start_date = 2026-09-08
end_date = 2026-09-09
product_type = BURST
beam_mode = IW
polarizations = VV
tracks = 154
orbit_direction = descending
coverage_threshold = 0.99
query_dir = INPUT/SENTINEL1/QUERY/main
download_dir = INPUT/SENTINEL1/main
download_threads = 2
max_retries = 3
retry_delay_seconds = 1
keep_burst_files = true
""")
            tops = config_dir / "gamma_sar_tops_slc.cfg"
            tops.write_text("""
[project]
root = ../..
[defaults]
opod_dir = -
range_looks = 7
azimuth_looks = 2
polarization = vv
workflow = safe
[case:main]
raw_dir = INPUT/SENTINEL1/main/Orbit_154
slc_dir = WORK/GAMMA/SLC_154
images = INPUT/SENTINEL1/QUERY/main/images_154
burst_table = WORK/GAMMA/S1_WORK_154/ref_burst_number_table_154
""")
            source = load_sentinel1_acquisition_config(acquisition)[0]
            target = load_slc_config(tops)[0]
        self.assertEqual(target.raw_dir, source.download_dir / "Orbit_154")
        self.assertEqual(target.images_file, source.query_dir / "images_154")

    def test_all_three_products_remain_discoverable_in_template(self) -> None:
        template = (ROOT / "config_examples/sentinel1_download.cfg").read_text(encoding="utf-8")
        for product in ("BURST", "SLC", "GRD"):
            self.assertIn(product, template)
        self.assertIn("gamma_sar_tops_slc.cfg", template)


if __name__ == "__main__":
    unittest.main()
