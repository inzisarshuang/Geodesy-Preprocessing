"""Tests for strict backend-neutral Sentinel-1 acquisition configuration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.acquisition.sentinel1.config import (
    load_sentinel1_acquisition_config,
    select_sentinel1_acquisition_case,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.models import (
    Sentinel1ProductType,
)


class Sentinel1AcquisitionConfigTests(unittest.TestCase):
    """Resolve project cases without accepting credentials or unsafe paths."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "project/AOI").mkdir(parents=True)
        (self.root / "project/AOI/area.kml").write_text("<kml/>", encoding="utf-8")
        self.config = self.root / "sentinel1_download.cfg"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, *, extra: str = "", case_body: str | None = None) -> None:
        body = case_body or """kml_file = AOI/area.kml
start_date = 2026-09-01
end_date = 2026-09-30
query_dir = INPUT/SENTINEL1/QUERY/main
download_dir = INPUT/SENTINEL1/main
"""
        self.config.write_text(
            f"""[project]
root = project

[defaults]
provider = asf
product_type = BURST
beam_mode = IW
polarizations = VV
tracks = auto
orbit_direction = auto
coverage_threshold = 0.999
download_threads = 2
max_retries = 3
retry_delay_seconds = 10
keep_burst_files = true

[case:main]
{body}
{extra}
""",
            encoding="utf-8",
        )

    def test_normalizes_defaults_paths_and_manual_selection(self) -> None:
        self._write(
            extra="""product_type = SLC
beam_mode = iw
polarizations = vv, vh
tracks = 154, 1
orbit_direction = descending
coverage_threshold = 0.95
download_threads = 4
max_retries = 5
retry_delay_seconds = 20
keep_burst_files = false
"""
        )

        case = select_sentinel1_acquisition_case(
            load_sentinel1_acquisition_config(self.config), None
        )

        project = self.root / "project"
        self.assertIs(case.product_type, Sentinel1ProductType.SLC)
        self.assertEqual(case.provider, "asf")
        self.assertEqual(case.beam_mode, "IW")
        self.assertEqual(case.polarizations, ("VV", "VH"))
        self.assertEqual(case.tracks, (1, 154))
        self.assertEqual(case.orbit_direction, "DESCENDING")
        self.assertEqual(case.coverage_threshold, 0.95)
        self.assertEqual(case.download_threads, 4)
        self.assertEqual(case.max_retries, 5)
        self.assertEqual(case.retry_delay_seconds, 20)
        self.assertFalse(case.keep_burst_files)
        self.assertEqual(case.kml_file, project / "AOI/area.kml")
        self.assertIsNone(case.bbox)
        self.assertEqual(case.query_dir, project / "INPUT/SENTINEL1/QUERY/main")
        self.assertEqual(case.download_dir, project / "INPUT/SENTINEL1/main")

    def test_supports_bbox_instead_of_kml_and_auto_polarization(self) -> None:
        self._write(
            extra="polarizations = auto\nproduct_type = GRD\nbeam_mode = EW",
            case_body="""bbox = -5.4, 39.6, -3.8, 41.1
start_date = 2026-09-01
end_date = 2026-09-30
query_dir = QUERY
download_dir = INPUT
""",
        )

        case = load_sentinel1_acquisition_config(self.config)[0]

        self.assertIsNone(case.kml_file)
        self.assertEqual(case.bbox, (-5.4, 39.6, -3.8, 41.1))
        self.assertIsNone(case.polarizations)
        self.assertIs(case.product_type, Sentinel1ProductType.GRD)

    def test_accepts_all_three_product_types_and_valid_beam_modes(self) -> None:
        accepted = (("BURST", "IW"), ("BURST", "EW"), ("SLC", "WV"), ("GRD", "S1"))
        for product_type, beam_mode in accepted:
            with self.subTest(product_type=product_type, beam_mode=beam_mode):
                self._write(extra=f"product_type = {product_type}\nbeam_mode = {beam_mode}")
                case = load_sentinel1_acquisition_config(self.config)[0]
                self.assertEqual(case.product_type.value, product_type)
                self.assertEqual(case.beam_mode, beam_mode)

    def test_requires_exactly_one_aoi_and_ordered_iso_dates(self) -> None:
        invalid_cases = {
            """kml_file = AOI/area.kml
bbox = -5, 39, -4, 40
start_date = 2026-09-01
end_date = 2026-09-30
query_dir = QUERY
download_dir = INPUT
""": "exactly one",
            """start_date = 2026-09-01
end_date = 2026-09-30
query_dir = QUERY
download_dir = INPUT
""": "exactly one",
            """kml_file = AOI/area.kml
start_date = 20260901
end_date = 2026-09-30
query_dir = QUERY
download_dir = INPUT
""": "YYYY-MM-DD",
            """kml_file = AOI/area.kml
start_date = 2026-10-01
end_date = 2026-09-30
query_dir = QUERY
download_dir = INPUT
""": "start_date.*end_date",
        }
        for body, message in invalid_cases.items():
            with self.subTest(message=message):
                self._write(case_body=body)
                with self.assertRaisesRegex(ValueError, message):
                    load_sentinel1_acquisition_config(self.config)

    def test_rejects_invalid_enums_lists_bounds_and_credentials(self) -> None:
        invalid = {
            "product_type = OCN": "product_type",
            "product_type = BURST\nbeam_mode = WV": "beam_mode",
            "polarizations = XX": "polarization",
            "polarizations = VV,VV": "unique",
            "tracks = 0": "positive integer",
            "tracks = 154,154": "duplicate",
            "orbit_direction = sideways": "orbit_direction",
            "coverage_threshold = 0": "coverage_threshold",
            "coverage_threshold = 1.1": "coverage_threshold",
            "download_threads = 0": "download_threads",
            "download_threads = 33": "download_threads",
            "max_retries = -1": "max_retries",
            "max_retries = 21": "max_retries",
            "retry_delay_seconds = -1": "retry_delay_seconds",
            "keep_burst_files = perhaps": "keep_burst_files",
            "provider = copernicus": "provider",
            "mystery = yes": "unknown key",
            "earthdata_username = user": "credential",
            "password = secret": "credential",
            "api_token = secret": "credential",
            "cookie_file = secret": "credential",
        }
        for fragment, message in invalid.items():
            with self.subTest(fragment=fragment):
                self._write(extra=fragment)
                with self.assertRaisesRegex(ValueError, message):
                    load_sentinel1_acquisition_config(self.config)

    def test_rejects_invalid_bbox_and_project_escape(self) -> None:
        self._write(
            case_body="""bbox = -3, 41, -5, 39
start_date = 2026-09-01
end_date = 2026-09-30
query_dir = QUERY
download_dir = INPUT
"""
        )
        with self.assertRaisesRegex(ValueError, "bbox"):
            load_sentinel1_acquisition_config(self.config)

        self._write()
        self.config.write_text(
            self.config.read_text().replace("download_dir = INPUT/SENTINEL1/main", "download_dir = ../outside"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "outside project root"):
            load_sentinel1_acquisition_config(self.config)

    def test_select_requires_name_for_multiple_cases(self) -> None:
        self._write()
        with self.config.open("a", encoding="utf-8") as stream:
            stream.write(
                """
[case:second]
kml_file = AOI/area.kml
start_date = 2026-10-01
end_date = 2026-10-02
query_dir = QUERY/second
download_dir = INPUT/second
"""
            )
        cases = load_sentinel1_acquisition_config(self.config)
        with self.assertRaisesRegex(ValueError, "--case is required"):
            select_sentinel1_acquisition_case(cases, None)
        self.assertEqual(select_sentinel1_acquisition_case(cases, "second").name, "second")
        with self.assertRaisesRegex(ValueError, "unknown Sentinel-1 case"):
            select_sentinel1_acquisition_case(cases, "missing")


if __name__ == "__main__":
    unittest.main()
