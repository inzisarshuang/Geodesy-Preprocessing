"""Tests for authenticated, resumable, atomic NISAR downloads."""

from __future__ import annotations

import hashlib
import json
import tempfile
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.acquisition.nisar.download import download_nisar
from geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5 import inspect_nisar_gslc
from geodesy_preprocessing.sar.acquisition.nisar.manifest import (
    NisarQueryManifest,
    query_fingerprint,
)
from geodesy_preprocessing.sar.acquisition.nisar.models import (
    NisarFrequencyMode,
    NisarGranule,
    NisarProductType,
    NisarSearchCase,
)
from geodesy_preprocessing.sar.acquisition.nisar.query import NisarQueryResult
from tests.fixtures.nisar_gslc import write_gslc
from tests.test_nisar_hdf5 import write_rslc


class _Response:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        error_text: str = "",
    ) -> None:
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.error_text = error_text

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.payload), max(1, chunk_size)):
            yield self.payload[offset : offset + chunk_size]

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.headers = {"Authorization": "Bearer AUTH-SECRET"}

    def get(self, url: str, *, headers: dict[str, str], stream: bool):
        self.calls.append((url, dict(headers)))
        if not self.responses:
            raise RuntimeError(f"network error url={url} Authorization=Bearer AUTH-SECRET")
        return self.responses.pop(0)


class _InterruptedSession:
    def get(self, url: str, *, headers: dict[str, str], stream: bool):
        raise KeyboardInterrupt


class NisarDownloadTests(unittest.TestCase):
    """Protect immutable HDF5 publication, safe retries, and credential redaction."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kml = self.root / "area.kml"
        self.kml.write_text("<kml>area</kml>", encoding="utf-8")
        self.query_dir = self.root / "QUERY"
        self.query_dir.mkdir()
        self.filtered = self.query_dir / "NISAR_RSLC_filtered.csv"
        self.filtered.write_text("granule_id\nproduct\n", encoding="utf-8")
        self.case = NisarSearchCase(
            name="main",
            project_root=self.root,
            kml_file=self.kml,
            start_date="2026-01-01",
            end_date="2026-02-01",
            product_type=NisarProductType.RSLC,
            band="L",
            polarizations=("HH",),
            provider="asf",
            frequency_mode=NisarFrequencyMode.MAIN_ONLY,
            tracks=(87,),
            frames=(22,),
            orbit_direction="DESCENDING",
            query_dir=self.query_dir,
            download_dir=self.root / "INPUT" / "NISAR",
        )
        source = self.root / "source.h5"
        write_rslc(source, bands={"L": {"A": ("HH",)}})
        self.payload = source.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _granule(
        self,
        *,
        granule_id: str = "NISAR_L_TEST",
        url: str = "https://example.test/product.h5",
        size: int | None = None,
        checksum: str | None = None,
        product_type: NisarProductType = NisarProductType.RSLC,
        orbit_direction: str = "DESCENDING",
        payload: bytes | None = None,
    ) -> NisarGranule:
        content = self.payload if payload is None else payload
        return NisarGranule(
            granule_id=granule_id,
            product_type=product_type,
            acquisition_date="2026-01-05",
            start_time="2026-01-05T10:20:30Z",
            end_time="2026-01-05T10:21:02Z",
            track=87,
            frame=22,
            orbit_direction=orbit_direction,
            footprint_wkt="POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))",
            size_bytes=len(content) if size is None else size,
            url=url,
            frequencies=("A",),
            polarizations=("HH",),
            checksum=checksum,
        )

    def _query(
        self,
        granules: tuple[NisarGranule, ...],
        case: NisarSearchCase | None = None,
    ) -> NisarQueryResult:
        case = self.case if case is None else case
        filtered = case.query_dir / f"NISAR_{case.product_type.value}_filtered.csv"
        if not filtered.exists():
            filtered.write_text("granule_id\nproduct\n", encoding="utf-8")
        kml_digest = hashlib.sha256(self.kml.read_bytes()).hexdigest()
        csv_digest = hashlib.sha256(filtered.read_bytes()).hexdigest()
        manifest = NisarQueryManifest(
            schema_version=1,
            case_name=case.name,
            fingerprint=query_fingerprint(case, kml_digest),
            kml_sha256=kml_digest,
            selected_granule_ids=tuple(item.granule_id for item in granules),
            artifact_digests=((filtered.name, csv_digest),),
        )
        return NisarQueryResult(case.query_dir, granules, granules, manifest)

    def _gslc(self) -> tuple[NisarSearchCase, bytes, NisarGranule]:
        source = self.root / "gslc-source.h5"
        write_gslc(source, bands={"L": {"A": ("HH",)}})
        payload = source.read_bytes()
        case = replace(
            self.case,
            product_type=NisarProductType.GSLC,
            orbit_direction="ASCENDING",
            download_dir=self.root / "INPUT" / "NISAR_GSLC",
        )
        granule = self._granule(
            granule_id="NISAR_L_GSLC_TEST",
            product_type=NisarProductType.GSLC,
            orbit_direction="ASCENDING",
            payload=payload,
        )
        return case, payload, granule

    def test_new_download_validates_hdf5_atomically_and_writes_source_manifest(self) -> None:
        granule = self._granule(checksum=f"sha256:{hashlib.sha256(self.payload).hexdigest()}")
        progress: list[str] = []

        result = download_nisar(
            self.case,
            self._query((granule,)),
            _Session([_Response(self.payload)]),
            progress_sink=progress.append,
        )

        product = self.case.download_dir / "product.h5"
        self.assertEqual(product.read_bytes(), self.payload)
        self.assertFalse((self.case.download_dir / "product.h5.part").exists())
        self.assertEqual(result.files, (product,))
        self.assertEqual(progress, ["[1/1] NISAR_L_TEST: downloading", "[1/1] NISAR_L_TEST: complete"])
        manifest = json.loads(result.source_manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["products"][0]["status"], "downloaded")
        self.assertEqual(manifest["products"][0]["sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(manifest["products"][0]["frequencies"], ["A"])

    def test_resumes_part_file_when_server_honors_range(self) -> None:
        granule = self._granule()
        self.case.download_dir.mkdir(parents=True)
        split = len(self.payload) // 3
        (self.case.download_dir / "product.h5.part").write_bytes(self.payload[:split])
        session = _Session(
            [
                _Response(
                    self.payload[split:],
                    status=206,
                    headers={"Content-Range": f"bytes {split}-{len(self.payload)-1}/{len(self.payload)}"},
                )
            ]
        )

        download_nisar(self.case, self._query((granule,)), session, progress_sink=lambda text: None)

        self.assertEqual(session.calls[0][1], {"Range": f"bytes={split}-"})
        self.assertEqual((self.case.download_dir / "product.h5").read_bytes(), self.payload)

    def test_download_resume_handles_ignored_range(self) -> None:
        granule = self._granule()
        self.case.download_dir.mkdir(parents=True)
        (self.case.download_dir / "product.h5.part").write_bytes(self.payload[:100])
        session = _Session([_Response(self.payload, status=200)])

        download_nisar(self.case, self._query((granule,)), session, progress_sink=lambda text: None)

        self.assertEqual(session.calls[0][1], {"Range": "bytes=100-"})
        self.assertEqual((self.case.download_dir / "product.h5").read_bytes(), self.payload)

    def test_rejects_size_checksum_and_hdf5_failures_before_atomic_rename(self) -> None:
        cases = (
            (self._granule(size=len(self.payload) + 1), self.payload, "size"),
            (self._granule(checksum="sha256:" + "0" * 64), self.payload, "checksum"),
            (self._granule(size=len(b"not an hdf5")), b"not an hdf5", "HDF5"),
        )
        for granule, payload, message in cases:
            with self.subTest(message=message):
                if self.case.download_dir.exists():
                    for path in self.case.download_dir.iterdir():
                        path.unlink()
                with self.assertRaisesRegex(ValueError, message):
                    download_nisar(
                        self.case,
                        self._query((granule,)),
                        _Session([_Response(payload)]),
                        progress_sink=lambda text: None,
                    )
                self.assertFalse((self.case.download_dir / "product.h5").exists())

    def test_rejects_duplicate_target_names_before_network_access(self) -> None:
        first = self._granule(granule_id="one", url="https://one.test/product.h5")
        second = self._granule(granule_id="two", url="https://two.test/product.h5")
        session = _Session([])

        with self.assertRaisesRegex(ValueError, "duplicate.*product.h5"):
            download_nisar(
                self.case,
                self._query((first, second)),
                session,
                progress_sink=lambda text: None,
            )
        self.assertEqual(session.calls, [])

    def test_rejects_empty_selection_and_hdf5_identity_mismatch(self) -> None:
        session = _Session([])
        with self.assertRaisesRegex(ValueError, "no NISAR granules selected"):
            download_nisar(
                self.case,
                self._query(()),
                session,
                progress_sink=lambda text: None,
            )
        self.assertEqual(session.calls, [])

        granule = self._granule(granule_id="WRONG_GRANULE")
        with self.assertRaisesRegex(ValueError, "granule identity"):
            download_nisar(
                self.case,
                self._query((granule,)),
                _Session([_Response(self.payload)]),
                progress_sink=lambda text: None,
            )

    def test_keyboard_interrupt_is_not_hidden_as_a_network_error(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            download_nisar(
                self.case,
                self._query((self._granule(),)),
                _InterruptedSession(),
                progress_sink=lambda text: None,
            )

    def test_download_error_redacts_credentials(self) -> None:
        url = "https://earth-user:earth-pass@example.test/product.h5?token=TOKEN-SECRET"
        granule = self._granule(url=url)
        progress: list[str] = []
        session = _Session([_Response(b"denied", status=401, error_text=url + " AUTH-SECRET")])

        with self.assertRaises(RuntimeError) as raised:
            download_nisar(
                self.case,
                self._query((granule,)),
                session,
                progress_sink=progress.append,
            )

        exposed = "".join(
            traceback.format_exception(
                type(raised.exception), raised.exception, raised.exception.__traceback__
            )
        ) + "\n".join(progress)
        for secret in ("earth-user", "earth-pass", "TOKEN-SECRET", "AUTH-SECRET"):
            self.assertNotIn(secret, exposed)
        self.assertIn("NISAR_L_TEST", exposed)

        with self.assertRaises(RuntimeError) as network_raised:
            download_nisar(
                self.case,
                self._query((granule,)),
                _Session([]),
                progress_sink=lambda text: None,
            )
        network_traceback = "".join(
            traceback.format_exception(
                type(network_raised.exception),
                network_raised.exception,
                network_raised.exception.__traceback__,
            )
        )
        for secret in ("earth-user", "earth-pass", "TOKEN-SECRET", "AUTH-SECRET"):
            self.assertNotIn(secret, network_traceback)

    def test_gslc_download_uses_gslc_inspector_and_computes_local_sha256(self) -> None:
        case, payload, granule = self._gslc()
        with mock.patch(
            "geodesy_preprocessing.sar.acquisition.nisar.download.inspect_nisar_gslc",
            wraps=inspect_nisar_gslc,
        ) as inspect_gslc:
            result = download_nisar(
                case,
                self._query((granule,), case),
                _Session([_Response(payload)]),
                progress_sink=lambda text: None,
            )

        inspect_gslc.assert_called_once()
        manifest = json.loads(result.source_manifest.read_text(encoding="utf-8"))
        record = manifest["products"][0]
        self.assertEqual(record["product_type"], "GSLC")
        self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertIsNone(record["source_checksum"])

    def test_gslc_download_rejects_rslc_masquerading_as_gslc(self) -> None:
        case, _, granule = self._gslc()
        granule = replace(granule, size_bytes=len(self.payload))

        with self.assertRaisesRegex(ValueError, "GSLC"):
            download_nisar(
                case,
                self._query((granule,), case),
                _Session([_Response(self.payload)]),
                progress_sink=lambda text: None,
            )

    def test_gslc_download_resume_and_interrupt_are_safe(self) -> None:
        case, payload, granule = self._gslc()
        case.download_dir.mkdir(parents=True)
        part = case.download_dir / "product.h5.part"
        split = len(payload) // 4
        part.write_bytes(payload[:split])

        session = _Session([_Response(payload, status=200)])
        download_nisar(
            case,
            self._query((granule,), case),
            session,
            progress_sink=lambda text: None,
        )
        self.assertEqual(session.calls[0][1], {"Range": f"bytes={split}-"})
        self.assertEqual((case.download_dir / "product.h5").read_bytes(), payload)

        (case.download_dir / "product.h5").unlink()
        prefix = payload[: max(1, len(payload) // 8)]

        class InterruptingResponse(_Response):
            def iter_content(self, chunk_size: int):
                yield prefix
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            download_nisar(
                case,
                self._query((granule,), case),
                _Session([InterruptingResponse(payload)]),
                progress_sink=lambda text: None,
            )
        self.assertEqual(part.read_bytes(), prefix)
        self.assertFalse((case.download_dir / "product.h5").exists())


if __name__ == "__main__":
    unittest.main()
