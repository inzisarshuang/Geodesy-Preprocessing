"""Tests for BURST group preflight and atomic SAFE publication."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import rasterio

from geodesy_preprocessing.sar.acquisition.sentinel1.manifest import (
    Sentinel1QueryManifest,
    aoi_sha256,
    query_fingerprint,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.merge import (
    merge_sentinel1_bursts,
    validate_burst_group,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.models import (
    Sentinel1AcquisitionCase,
    Sentinel1Product,
    Sentinel1ProductType,
)
from geodesy_preprocessing.sar.acquisition.sentinel1.query import Sentinel1QueryResult


class Sentinel1MergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kml = self.root / "a.kml"
        self.kml.write_text("<kml>area</kml>")
        self.query_dir = self.root / "QUERY"
        self.query_dir.mkdir()
        self.download = self.root / "INPUT"
        self.case = Sentinel1AcquisitionCase(
            name="main", project_root=self.root, kml_file=self.kml, bbox=None,
            start_date="2026-09-08", end_date="2026-09-09",
            product_type=Sentinel1ProductType.BURST, beam_mode="IW", polarizations=("VV",),
            provider="asf", tracks=(154,), orbit_direction="DESCENDING", coverage_threshold=.5,
            query_dir=self.query_dir, download_dir=self.download, download_threads=2,
            max_retries=1, retry_delay_seconds=0, keep_burst_files=True,
        )
        self.products = (self._product("B1", "IW1", 7), self._product("B2", "IW2", 8, supplemental=True))
        orbit = self.download / "Orbit_154"
        orbit.mkdir(parents=True)
        sized = []
        for product in self.products:
            with rasterio.open(orbit / product.file_name, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint16") as dataset:
                dataset.write(np.ones((1, 2, 2), dtype="uint16"))
            sized.append(replace(product, size_bytes=(orbit / product.file_name).stat().st_size))
        self.products = tuple(sized)
        self.query = self._query(self.products)
        self.source_manifest = self._source_manifest(self.products)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _product(self, identity, swath, burst_id, supplemental=False):
        return Sentinel1Product(
            product_id=identity, product_type=Sentinel1ProductType.BURST,
            acquisition_date="2026-09-08", start_time=f"2026-09-08T06:00:{burst_id:02d}Z",
            end_time=f"2026-09-08T06:00:{burst_id+1:02d}Z", relative_orbit=154,
            absolute_orbit=65001, orbit_direction="DESCENDING", beam_mode="IW",
            polarizations=("VV",), footprint_wkt="POLYGON ((0 0,1 0,1 1,0 1,0 0))",
            file_name=f"{identity}.tiff", size_bytes=1, url=f"https://example.test/{identity}.tiff",
            swath=swath, burst_index=burst_id, relative_burst_id=burst_id,
            full_burst_id=f"154_{swath}_{burst_id}", merge_group="GROUP", roi_match=not supplemental,
            supplemental=supplemental,
        )

    def _query(self, products):
        selected = self.query_dir / "S1_BURST_selected.csv"
        selected.write_text("selected\n")
        digest = aoi_sha256(self.case)
        manifest = Sentinel1QueryManifest(
            1, "main", query_fingerprint(self.case, digest), digest,
            tuple(item.product_id for item in products),
            ((selected.name, hashlib.sha256(selected.read_bytes()).hexdigest()),),
        )
        return Sentinel1QueryResult(self.query_dir, tuple(products), tuple(products), None, manifest)

    def _source_manifest(self, products):
        records = []
        for product in products:
            path = self.download / "Orbit_154" / product.file_name
            records.append({
                "product_id": product.product_id, "file": path.relative_to(self.download).as_posix(),
                "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        path = self.download / "source_manifest.json"
        path.write_text(json.dumps({"schema_version": 1, "case_name": "main", "query_fingerprint": self.query.manifest.fingerprint, "products": records}))
        return path

    def test_group_validation_rejects_duplicates_incomplete_identity_and_missing_sources(self) -> None:
        group = validate_burst_group(self.products, self.download, self.source_manifest)
        self.assertEqual(tuple(item.product_id for item in group.products), ("B1", "B2"))
        self.assertEqual(group.key, ("2026-09-08", 154, "GROUP"))

        with self.assertRaisesRegex(ValueError, "duplicate.*burst"):
            validate_burst_group((self.products[0], self.products[0]), self.download, self.source_manifest)
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_burst_group((replace(self.products[0], full_burst_id=None),), self.download, self.source_manifest)
        (self.download / "Orbit_154" / "B2.tiff").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_burst_group(self.products, self.download, self.source_manifest)

    def test_atomically_publishes_valid_safe_and_skips_identical_rerun(self) -> None:
        source_hashes = {item.file_name: hashlib.sha256((self.download / "Orbit_154" / item.file_name).read_bytes()).hexdigest() for item in self.products}
        calls = []

        def runner(granules, work_dir):
            calls.append(tuple(granules))
            safe = work_dir / "S1_MERGED.SAFE"
            (safe / "annotation").mkdir(parents=True)
            (safe / "measurement").mkdir()
            (safe / "manifest.safe").write_text("manifest")
            (safe / "annotation" / "a.xml").write_text("annotation")
            (safe / "measurement" / "m.tiff").write_bytes(b"measurement")
            return safe

        progress = []
        result = merge_sentinel1_bursts(self.case, self.query, self.source_manifest, runner=runner, progress_sink=progress.append)
        safe = self.download / "Orbit_154" / "S1_MERGED.SAFE"
        self.assertEqual(result.safe_directories, (safe,))
        self.assertTrue((safe / "GEODESY_SOURCE.json").is_file())
        self.assertTrue(result.safe_manifest.is_file())
        self.assertEqual(calls, [("B1", "B2")])
        self.assertEqual(progress, ["[1/1] 2026-09-08 track 154 GROUP: merging", "[1/1] 2026-09-08 track 154 GROUP: complete"])
        for name, digest in source_hashes.items():
            self.assertEqual(hashlib.sha256((self.download / "Orbit_154" / name).read_bytes()).hexdigest(), digest)

        second = merge_sentinel1_bursts(self.case, self.query, self.source_manifest, runner=lambda *args: self.fail("runner called"), progress_sink=lambda _: None)
        self.assertEqual(second.safe_directories, (safe,))

    def test_failure_does_not_publish_partial_safe(self) -> None:
        def runner(granules, work_dir):
            safe = work_dir / "BROKEN.SAFE"
            safe.mkdir()
            (safe / "manifest.safe").write_text("partial")
            raise RuntimeError("injected failure")

        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            merge_sentinel1_bursts(self.case, self.query, self.source_manifest, runner=runner, progress_sink=lambda _: None)
        self.assertFalse((self.download / "Orbit_154" / "BROKEN.SAFE").exists())
        self.assertEqual(tuple(self.download.glob(".merge-*.stage")), ())

    def test_merge_rejects_stale_safe_signature(self) -> None:
        def runner(granules, work_dir):
            safe = work_dir / "S1_MERGED.SAFE"
            (safe / "annotation").mkdir(parents=True)
            (safe / "measurement").mkdir()
            (safe / "manifest.safe").write_text("manifest")
            (safe / "annotation" / "a.xml").write_text("annotation")
            (safe / "measurement" / "m.tiff").write_bytes(b"measurement")
            return safe

        merge_sentinel1_bursts(self.case, self.query, self.source_manifest, runner=runner, progress_sink=lambda _: None)
        marker = self.download / "Orbit_154" / "S1_MERGED.SAFE" / "GEODESY_SOURCE.json"
        document = json.loads(marker.read_text())
        document["input_signature"] = "0" * 64
        marker.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "stale SAFE.*signature"):
            merge_sentinel1_bursts(self.case, self.query, self.source_manifest, runner=runner, progress_sink=lambda _: None)


if __name__ == "__main__":
    unittest.main()
