"""Integration tests for case_root across GAMMA preprocessing CFG readers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_config import (
    load_gamma_radar_coreg_config,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.geo_ref_config import (
    load_gamma_radar_geo_ref_config,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_config import (
    load_coreg_config,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_config import (
    load_geo_ref_config,
)
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.config import (
    load_crop_config,
)
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    load_gamma_radar_slc_config,
)
from geodesy_preprocessing.sar.slc_generation.gamma.tops.config import load_slc_config


class CaseLayoutConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "study"
        self.config_dir = self.project / "S1/ASCENDING_128/config/preprocessing"
        self.config_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, name: str, body: str) -> Path:
        path = self.config_dir / name
        path.write_text("[project]\nroot = ../../../..\n" + body, encoding="utf-8")
        return path

    def test_tops_readers_share_one_canonical_case_root(self) -> None:
        slc = load_slc_config(
            self._write(
                "tops_slc.cfg",
                """
[defaults]
opod_dir = -
range_looks = 7
azimuth_looks = 2
polarization = vv
workflow = safe
[case:main]
case_root = S1/ASCENDING_128
raw_dir = S1/DOWNLOAD/ascending/Orbit_128
slc_dir = S1/ASCENDING_128/PRE/GAMMA/SLC
images = S1/QUERY/ascending/images_128
burst_table = S1/ASCENDING_128/PRE/ref_burst_number_table_128
""",
            )
        )[0]
        geometry = load_geo_ref_config(
            self._write(
                "tops_geo.cfg",
                """
[defaults]
dem_dir = TOPOGRAPHY/jishishan_cop30/GAMMA
range_looks = 7
azimuth_looks = 2
latitude_oversampling = auto
longitude_oversampling = auto
reference_flag = 1
incidence_flag = 0
[case:main]
case_root = S1/ASCENDING_128
reference = 20231226
slc_dir = S1/ASCENDING_128/PRE/GAMMA/SLC
output_dir = S1/ASCENDING_128/PRE/GAMMA/GEOMETRY
""",
            )
        )[0]
        rslc = load_coreg_config(
            self._write(
                "tops_coreg.cfg",
                """
[defaults]
range_looks = 7
azimuth_looks = 2
coherence_threshold = 0.8
fraction_threshold = 0.01
phase_stdev_threshold = 0.8
id_mode = date
npoly = 4
bridge_mode = none
[case:main]
case_root = S1/ASCENDING_128
slc_dir = S1/ASCENDING_128/PRE/GAMMA/SLC
dem_geocode_dir = S1/ASCENDING_128/PRE/GAMMA/GEOMETRY
output_dir = S1/ASCENDING_128/PRE/GAMMA/RSLC
reference = 20231226
images = S1/QUERY/ascending/images_128
""",
            )
        )[0]

        root = self.project / "S1/ASCENDING_128"
        self.assertEqual((slc.case_root, geometry.case_root, rslc.case_root), (root, root, root))
        self.assertEqual(slc.slc_dir, root / "PRE/GAMMA/SLC")
        self.assertEqual(geometry.output_dir, root / "PRE/GAMMA/GEOMETRY")
        self.assertEqual(rslc.output_dir, root / "PRE/GAMMA/RSLC")

    def test_radar_grid_and_subset_readers_share_one_canonical_case_root(self) -> None:
        prefix = "SAOCOM/GUIZHOU/PRE/GAMMA"
        slc = load_gamma_radar_slc_config(
            self._write(
                "grid_slc.cfg",
                f"""
[case:main]
case_root = SAOCOM/GUIZHOU
sensor = saocom
raw_dir = SAOCOM/RAW/guizhou
slc_dir = {prefix}/SLC
images_file = {prefix}/SLC/images_saocom
""",
            )
        )[0]
        geometry = load_gamma_radar_geo_ref_config(
            self._write(
                "grid_geo.cfg",
                f"""
[case:main]
case_root = SAOCOM/GUIZHOU
reference_date = 20201215
primary_polarization = HH
slc_dir = {prefix}/SLC
images_file = {prefix}/SLC/images_saocom
dem = TOPOGRAPHY/guizhou_cop30/GAMMA/guizhou_cop30.dem
dem_par = TOPOGRAPHY/guizhou_cop30/GAMMA/guizhou_cop30.dem.par
output_dir = {prefix}/GEOMETRY
""",
            )
        )[0]
        rslc = load_gamma_radar_coreg_config(
            self._write(
                "grid_coreg.cfg",
                f"""
[case:main]
case_root = SAOCOM/GUIZHOU
sensor = saocom
frequency = L
reference_date = 20201215
primary_polarization = HH
selected_polarizations = HH,HV
slc_dir = {prefix}/SLC
images_file = {prefix}/SLC/images_saocom
reference_geometry = {prefix}/GEOMETRY/reference_geometry.json
output_dir = {prefix}/RSLC
""",
            )
        )[0]
        subset = load_crop_config(
            self._write(
                "crop.cfg",
                f"""
[defaults]
polarizations = HH,HV
require_full_aoi_coverage = true
min_buffer_coverage = 0.95
[case:main]
case_root = SAOCOM/GUIZHOU
registered_stack = {prefix}/RSLC/registered_stack.json
geometry_dir = {prefix}/GEOMETRY
kml_file = SAOCOM/GUIZHOU/ROI.kml
output_dir = {prefix}/RSLC_SUBSET
""",
            ),
            "main",
        )

        root = self.project / "SAOCOM/GUIZHOU"
        self.assertEqual(
            (slc.case_root, geometry.case_root, rslc.case_root, subset.case_root),
            (root, root, root, root),
        )
        self.assertEqual(subset.output_dir, root / "PRE/GAMMA/RSLC_SUBSET")

    def test_case_root_rejects_legacy_work_output(self) -> None:
        config = self._write(
            "tops_coreg_bad.cfg",
            """
[defaults]
range_looks = 7
azimuth_looks = 2
coherence_threshold = 0.8
fraction_threshold = 0.01
phase_stdev_threshold = 0.8
id_mode = date
npoly = 4
bridge_mode = none
[case:main]
case_root = S1/ASCENDING_128
slc_dir = S1/ASCENDING_128/PRE/GAMMA/SLC
dem_geocode_dir = S1/ASCENDING_128/PRE/GAMMA/GEOMETRY
output_dir = S1/WORK/GAMMA/ascending/RSLC
reference = 20231226
images = S1/QUERY/ascending/images_128
""",
        )
        with self.assertRaisesRegex(ValueError, "canonical case path"):
            load_coreg_config(config)


if __name__ == "__main__":
    unittest.main()
