"""Tests for secure OpenTopography downloads and raster metadata."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
import requests
from rasterio.transform import from_bounds

from geodesy_preprocessing.dem.extent import BoundingBox
from geodesy_preprocessing.dem.acquisition.opentopography import (
    download_dem,
    normalize_dem_type,
    resolve_api_key,
    validate_api_url,
    validate_geotiff,
    write_metadata,
)


class FakeResponse:
    """Small streaming response used without network access."""

    def __init__(self, body: bytes, *, fail_during_stream: bool = False) -> None:
        self.body = body
        self.fail_during_stream = fail_during_stream
        self.ok = True
        self.status_code = 200
        self.headers = {
            "Content-Type": "image/tiff",
            "Content-Length": str(len(body)),
        }
        self.text = ""
        self.closed = False

    def iter_content(self, chunk_size: int):
        split = max(1, min(chunk_size, len(self.body) // 2 or 1))
        yield self.body[:split]
        if self.fail_during_stream:
            raise OSError("connection interrupted")
        yield self.body[split:]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Record one request and return a predetermined response."""

    def __init__(self, body: bytes, *, fail_during_stream: bool = False) -> None:
        self.response = FakeResponse(body, fail_during_stream=fail_during_stream)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    def close(self) -> None:
        self.closed = True


class OpenTopographyTests(unittest.TestCase):
    """Verify identifiers, credentials, downloads, and safe metadata."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bbox = BoundingBox(-5.0, 40.0, -4.0, 41.0)
        self.valid_tiff = self.root / "valid.tif"
        profile = {
            "driver": "GTiff",
            "height": 10,
            "width": 10,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:4326",
            "transform": from_bounds(-5.0, 40.0, -4.0, 41.0, 10, 10),
            "nodata": -9999.0,
        }
        with rasterio.open(self.valid_tiff, "w", **profile) as dataset:
            dataset.write(np.ones((10, 10), dtype=np.float32), 1)
        self.valid_body = self.valid_tiff.read_bytes()
        self.metadata = self.root / "dem.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def download_arguments(self) -> dict[str, object]:
        return {
            "api_url": "https://portal.opentopography.org/API/globaldem",
            "dem_type": "COP30",
            "bbox": self.bbox,
            "api_key": "secret-value",
            "output_format": "GTiff",
            "timeout_connect_sec": 30.0,
            "timeout_read_sec": 60.0,
            "retries": 0,
            "backoff_factor": 0.0,
            "chunk_mb": 1,
        }

    def test_normalizes_only_supported_dataset_names(self) -> None:
        self.assertEqual(normalize_dem_type("SRTM_GL1_Ellip"), "SRTMGL1_E")
        self.assertEqual(normalize_dem_type("COP30"), "COP30")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            normalize_dem_type("invented")

    def test_key_source_is_reported_without_value(self) -> None:
        with patch.dict(os.environ, {"TEST_OT_KEY": "secret-value"}, clear=False):
            key, source = resolve_api_key(
                {"api_key_env": "TEST_OT_KEY", "api_key": ""}
            )
        self.assertEqual(key, "secret-value")
        self.assertEqual(source, "environment:TEST_OT_KEY")
        self.assertNotIn(key, source)

    def test_untrusted_api_url_is_rejected(self) -> None:
        for url in (
            "http://portal.opentopography.org/API/globaldem",
            "https://example.com/globaldem",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                ValueError, "HTTPS|host"
            ):
                validate_api_url(url)

    def test_validates_geotiff_coverage(self) -> None:
        info = validate_geotiff(self.valid_tiff, self.bbox)
        self.assertEqual(info["crs"], "EPSG:4326")
        self.assertEqual((info["height"], info["width"]), (10, 10))

    def test_invalid_download_never_replaces_existing_output(self) -> None:
        output = self.root / "dem.tif"
        output.write_bytes(b"original")
        before = (output.read_bytes(), output.stat().st_mtime_ns)
        session = FakeSession(body=b"not-a-tiff")
        with self.assertRaisesRegex(RuntimeError, "GeoTIFF|TIFF"):
            download_dem(
                **self.download_arguments(),
                output_path=output,
                overwrite=True,
                session=session,
            )
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
        self.assertEqual(list(self.root.glob(".dem.tif.*.part")), [])

    def test_interrupted_download_removes_temporary_file(self) -> None:
        output = self.root / "interrupted.tif"
        session = FakeSession(self.valid_body, fail_during_stream=True)
        with self.assertRaisesRegex(OSError, "interrupted"):
            download_dem(
                **self.download_arguments(),
                output_path=output,
                session=session,
            )
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".interrupted.tif.*.part")), [])

    def test_network_exception_never_exposes_api_key(self) -> None:
        output = self.root / "network-error.tif"
        session = FakeSession(self.valid_body)
        session.get = unittest.mock.Mock(
            side_effect=requests.ConnectionError(
                "failed URL https://portal.opentopography.org/API/globaldem?"
                "demtype=COP30&API_Key=secret-value"
            )
        )
        with self.assertRaises(RuntimeError) as raised:
            download_dem(
                **self.download_arguments(), output_path=output, session=session
            )
        message = str(raised.exception)
        self.assertNotIn("secret-value", message)
        self.assertIn("<redacted>", message)
        self.assertFalse(output.exists())

    def test_http_error_body_never_exposes_api_key(self) -> None:
        output = self.root / "http-error.tif"
        session = FakeSession(self.valid_body)
        session.response.ok = False
        session.response.status_code = 401
        session.response.text = "invalid API_Key=secret-value"
        with self.assertRaises(RuntimeError) as raised:
            download_dem(
                **self.download_arguments(), output_path=output, session=session
            )
        message = str(raised.exception)
        self.assertNotIn("secret-value", message)
        self.assertIn("API_Key=<redacted>", message)
        self.assertFalse(output.exists())

    def test_successful_injected_session_publishes_valid_raster(self) -> None:
        output = self.root / "downloaded.tif"
        session = FakeSession(self.valid_body)
        terminal = StringIO()
        with redirect_stdout(terminal):
            result = download_dem(
                **self.download_arguments(), output_path=output, session=session
            )
        self.assertEqual(result, output)
        self.assertEqual(validate_geotiff(output, self.bbox)["width"], 10)
        self.assertNotIn("secret-value", terminal.getvalue())

    def test_metadata_contains_raster_summary_but_not_api_key(self) -> None:
        write_metadata(
            self.metadata,
            config_path=self.root / "opentopo_dem_download.cfg",
            input_mode="bbox",
            input_kml=None,
            kml_placemark=None,
            source_bbox=self.bbox,
            request_bbox=self.bbox,
            buffer_km=0.0,
            estimated_area_km2=1.0,
            api_url="https://portal.opentopography.org/API/globaldem",
            dem_type="COP30",
            output_format="GTiff",
            output_path=self.valid_tiff,
            api_key_source="environment:TEST_OT_KEY",
        )
        payload = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", payload)
        self.assertEqual(payload["raster"]["crs"], "EPSG:4326")


if __name__ == "__main__":
    unittest.main()
