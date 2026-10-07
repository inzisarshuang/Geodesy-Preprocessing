"""Tests for strict backend-neutral NISAR acquisition configuration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.acquisition.nisar.config import (
    load_nisar_config,
    select_nisar_case,
)
from geodesy_preprocessing.sar.acquisition.nisar.models import (
    NisarFrequencyMode,
    NisarProductType,
)


class NisarAcquisitionConfigTests(unittest.TestCase):
    """Resolve safe project cases without accepting credentials."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "study").mkdir()
        self.config = self.root / "nisar_download.cfg"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, extra: str = "", defaults: str = "") -> None:
        self.config.write_text(
            f"""[project]
root = study
[defaults]
product_type = RSLC
band = L
provider = asf
frequency_mode = main_only
tracks = auto
frames = auto
orbit_direction = auto
{defaults}
[case:main]
kml_file = area.kml
start_date = 2026-01-01
end_date = 2026-02-01
polarizations = HH, HV
query_dir = QUERY/NISAR
download_dir = INPUT/NISAR
{extra}
""",
            encoding="utf-8",
        )

    def test_defaults_paths_lists_and_frequency_mode(self) -> None:
        self._write(
            extra="frequency_mode = main_plus_auxiliary\ntracks = 12, 4\nframes = 8, 2\norbit_direction = ascending"
        )
        case = select_nisar_case(load_nisar_config(self.config), None)
        project = self.root / "study"
        self.assertIs(case.product_type, NisarProductType.RSLC)
        self.assertEqual(case.band, "L")
        self.assertEqual(case.provider, "asf")
        self.assertIs(case.frequency_mode, NisarFrequencyMode.MAIN_PLUS_AUXILIARY)
        self.assertEqual(case.tracks, (4, 12))
        self.assertEqual(case.frames, (2, 8))
        self.assertEqual(case.orbit_direction, "ASCENDING")
        self.assertEqual(case.polarizations, ("HH", "HV"))
        self.assertEqual(case.kml_file, project / "area.kml")
        self.assertEqual(case.query_dir, project / "QUERY/NISAR")
        self.assertEqual(case.download_dir, project / "INPUT/NISAR")

    def test_requires_kml_dates_and_polarizations_and_valid_date_order(self) -> None:
        for key in ("kml_file", "start_date", "end_date", "polarizations"):
            with self.subTest(key=key):
                self._write()
                text = "\n".join(
                    line for line in self.config.read_text().splitlines() if not line.startswith(f"{key} =")
                )
                self.config.write_text(text)
                with self.assertRaisesRegex(ValueError, "missing keys"):
                    load_nisar_config(self.config)
        self._write()
        self.config.write_text(
            self.config.read_text().replace(
                "start_date = 2026-01-01", "start_date = 2026-03-01"
            )
        )
        with self.assertRaisesRegex(ValueError, "start_date.*end_date"):
            load_nisar_config(self.config)

    def test_rejects_invalid_enums_lists_unknown_keys_and_credentials(self) -> None:
        invalid = {
            "frequency_mode = both": "frequency_mode",
            "tracks = 0": "positive integer",
            "frames = 2,2": "duplicate",
            "orbit_direction = sideways": "orbit_direction",
            "band = X": "band",
            "provider = nasa": "provider",
            "product_type = GUNW": "product_type",
            "mystery = yes": "unknown key",
            "password = secret": "credential",
            "api_token = secret": "credential",
            "cookie_file = secret": "credential",
        }
        for fragment, message in invalid.items():
            with self.subTest(fragment=fragment):
                self._write(extra=fragment)
                with self.assertRaisesRegex(ValueError, message):
                    load_nisar_config(self.config)

    def test_accepts_gslc_as_an_explicit_product_type(self) -> None:
        self._write()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "product_type = RSLC", "product_type = GSLC"
            ),
            encoding="utf-8",
        )

        case = select_nisar_case(load_nisar_config(self.config), None)

        self.assertIs(case.product_type, NisarProductType.GSLC)

    def test_paths_cannot_escape_project_root(self) -> None:
        self._write()
        self.config.write_text(
            self.config.read_text().replace(
                "download_dir = INPUT/NISAR", "download_dir = ../outside"
            )
        )
        with self.assertRaisesRegex(ValueError, "outside project root"):
            load_nisar_config(self.config)


if __name__ == "__main__":
    unittest.main()
