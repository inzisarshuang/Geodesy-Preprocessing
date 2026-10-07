"""Tests for validated, resumable, secret-free Sentinel-1 downloads."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import traceback
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import rasterio

from geodesy_preprocessing.sar.acquisition.sentinel1.download import (
    download_sentinel1,
    validate_sentinel1_download,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.manifest import (
    Sentinel1QueryManifest,
    aoi_sha256,
    query_fingerprint,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.models import (
    Sentinel1AcquisitionCase,
    Sentinel1Product,
    Sentinel1ProductType,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.query import Sentinel1QueryResult


class _Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def iter_content(self, chunk_size):
        yield self.payload

    def close(self):
        pass


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers, stream):
        self.calls.append((url, dict(headers)))
        if not self.responses:
            raise RuntimeError(f"failure at {url} token=SECRET")
        return self.responses.pop(0)


def _zip_payload(root: str, product_type: Sentinel1ProductType) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"{root}.SAFE/manifest.safe", "manifest")
        archive.writestr(f"{root}.SAFE/measurement/{root.lower()}-001.tiff", b"measurement")
        if product_type is Sentinel1ProductType.SLC:
            archive.writestr(f"{root}.SAFE/annotation/{root.lower()}-001.xml", "annotation")
    return output.getvalue()


class Sentinel1DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kml = self.root / "a.kml"
        self.kml.write_text("<kml>area</kml>")
        self.query_dir = self.root / "QUERY"
        self.query_dir.mkdir()
        self.case = Sentinel1AcquisitionCase(
            name="main", project_root=self.root, kml_file=self.kml, bbox=None,
            start_date="2026-09-08", end_date="2026-09-09",
            product_type=Sentinel1ProductType.SLC, beam_mode="IW", polarizations=("VV",),
            provider="asf", tracks=(154,), orbit_direction="DESCENDING", coverage_threshold=.99,
            query_dir=self.query_dir, download_dir=self.root / "INPUT", download_threads=2,
            max_retries=1, retry_delay_seconds=0, keep_burst_files=True,
        )
        self.payload = _zip_payload("S1_TEST_SLC", Sentinel1ProductType.SLC)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _product(self, *, product_type=Sentinel1ProductType.SLC, payload=None, url="https://example.test/S1_TEST_SLC.zip"):
        payload = self.payload if payload is None else payload
        identity = "S1_TEST_SLC" if product_type is Sentinel1ProductType.SLC else "S1_TEST_GRD"
        filename = Path(url.split("?", 1)[0]).name
        return Sentinel1Product(
            product_id=identity, product_type=product_type, acquisition_date="2026-09-08",
            start_time="2026-09-08T06:00:00Z", end_time="2026-09-08T06:00:25Z",
            relative_orbit=154, absolute_orbit=65001, orbit_direction="DESCENDING",
            beam_mode="IW", polarizations=("VV",), footprint_wkt="POLYGON ((0 0,1 0,1 1,0 1,0 0))",
            file_name=filename, size_bytes=len(payload), url=url,
        )

    def _query(self, products):
        selected = self.query_dir / "S1_SLC_selected.csv"
        selected.write_text("selected\n")
        digest = aoi_sha256(self.case)
        manifest = Sentinel1QueryManifest(
            1, self.case.name, query_fingerprint(self.case, digest), digest,
            tuple(item.product_id for item in products),
            ((selected.name, hashlib.sha256(selected.read_bytes()).hexdigest()),),
        )
        return Sentinel1QueryResult(self.query_dir, tuple(products), tuple(products), None, manifest)

    def test_validators_accept_expected_slc_grd_and_burst_formats(self) -> None:
        slc = self._product()
        slc_path = self.root / slc.file_name
        slc_path.write_bytes(self.payload)
        self.assertEqual(validate_sentinel1_download(slc, slc_path)["kind"], "SLC_SAFE_ZIP")

        grd_payload = _zip_payload("S1_TEST_GRD", Sentinel1ProductType.GRD)
        grd = self._product(product_type=Sentinel1ProductType.GRD, payload=grd_payload, url="https://example.test/S1_TEST_GRD.zip")
        grd_path = self.root / grd.file_name
        grd_path.write_bytes(grd_payload)
        self.assertEqual(validate_sentinel1_download(grd, grd_path)["kind"], "GRD_SAFE_ZIP")

        burst_path = self.root / "BURST_ID.tiff"
        with rasterio.open(
            burst_path, "w", driver="GTiff", width=3, height=2, count=1,
            dtype="uint16", transform=rasterio.Affine(1, 0, 10, 0, -1, 20),
        ) as dataset:
            dataset.write(np.ones((1, 2, 3), dtype="uint16"))
        burst = replace(slc, product_id="BURST_ID", product_type=Sentinel1ProductType.BURST, file_name=burst_path.name, size_bytes=burst_path.stat().st_size)
        self.assertEqual(validate_sentinel1_download(burst, burst_path)["kind"], "BURST_RASTER")

    def test_existing_download_requires_format_and_identity(self) -> None:
        product = self._product()
        target = self.case.download_dir / "Orbit_154" / product.file_name
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x" * len(self.payload))
        session = _Session([])
        with self.assertRaisesRegex(ValueError, "ZIP|archive"):
            download_sentinel1(self.case, self._query((product,)), session, progress_sink=lambda _: None)
        self.assertEqual(session.calls, [])

        wrong = _zip_payload("OTHER_PRODUCT", Sentinel1ProductType.SLC)
        target.write_bytes(wrong)
        product = replace(product, size_bytes=len(wrong))
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_sentinel1_download(product, target)

    def test_provider_size_is_advisory_after_structural_validation(self) -> None:
        burst_path = self.root / "S1_TEST_BURST.tiff"
        with rasterio.open(
            burst_path, "w", driver="GTiff", width=3, height=2, count=1,
            dtype="uint16", transform=rasterio.Affine(1, 0, 10, 0, -1, 20),
        ) as dataset:
            dataset.write(np.ones((1, 2, 3), dtype="uint16"))
        with burst_path.open("r+b") as stream:
            stream.truncate(139904606)
        burst = replace(
            self._product(),
            product_id="S1_TEST_BURST",
            product_type=Sentinel1ProductType.BURST,
            file_name=burst_path.name,
            size_bytes=139882080,
        )

        validation = validate_sentinel1_download(burst, burst_path)

        self.assertEqual(validation["provider_size_bytes"], 139882080)
        self.assertEqual(validation["actual_size_bytes"], 139904606)
        self.assertFalse(validation["size_matches_provider"])

        payload = self.payload + (b"\0" * 100)
        product = replace(self._product(), size_bytes=len(self.payload))
        result = download_sentinel1(
            self.case,
            self._query((product,)),
            _Session([_Response(payload)]),
            progress_sink=lambda _: None,
        )
        record = json.loads(result.source_manifest.read_text())["products"][0]
        self.assertEqual(record["provider_size_bytes"], len(self.payload))
        self.assertEqual(record["size_bytes"], len(payload))

    def test_sentinel1_download_resume_and_redaction(self) -> None:
        product = self._product(url="https://user:pass@example.test/S1_TEST_SLC.zip?token=TOKEN")
        orbit = self.case.download_dir / "Orbit_154"
        orbit.mkdir(parents=True)
        split = len(self.payload) // 2
        part = orbit / f"{product.file_name}.part"
        part.write_bytes(self.payload[:split])
        session = _Session([_Response(self.payload[split:], 206, {"Content-Range": f"bytes {split}-{len(self.payload)-1}/{len(self.payload)}"})])
        progress = []
        result = download_sentinel1(self.case, self._query((product,)), session, progress_sink=progress.append)
        self.assertEqual(session.calls[0][1], {"Range": f"bytes={split}-"})
        self.assertEqual(result.files[0].read_bytes(), self.payload)
        manifest = json.loads(result.source_manifest.read_text())
        self.assertNotIn("url", manifest["products"][0])
        self.assertEqual(manifest["products"][0]["sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(progress, ["[1/1] S1_TEST_SLC: downloading", "[1/1] S1_TEST_SLC: complete"])

        broken = replace(product, file_name="BROKEN.zip", url="https://user:pass@example.test/BROKEN.zip?token=TOKEN")
        with self.assertRaises(RuntimeError) as raised:
            download_sentinel1(self.case, self._query((broken,)), _Session([]), progress_sink=lambda _: None)
        trace = "".join(traceback.format_exception(type(raised.exception), raised.exception, raised.exception.__traceback__))
        for secret in ("user", "pass", "TOKEN", "SECRET"):
            self.assertNotIn(secret, trace)

    def test_server_ignoring_range_restarts_and_ctrl_c_preserves_part(self) -> None:
        product = self._product()
        orbit = self.case.download_dir / "Orbit_154"
        orbit.mkdir(parents=True)
        part = orbit / f"{product.file_name}.part"
        part.write_bytes(self.payload[:20])
        download_sentinel1(self.case, self._query((product,)), _Session([_Response(self.payload)]), progress_sink=lambda _: None)
        self.assertEqual((orbit / product.file_name).read_bytes(), self.payload)

        target = orbit / product.file_name
        target.unlink()
        part.write_bytes(b"resume")

        class Interrupted:
            def get(self, *args, **kwargs):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            download_sentinel1(self.case, self._query((product,)), Interrupted(), progress_sink=lambda _: None)
        self.assertEqual(part.read_bytes(), b"resume")


if __name__ == "__main__":
    unittest.main()
