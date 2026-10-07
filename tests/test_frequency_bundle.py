"""Tests for linking complete NISAR LA/LB registered stacks."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.common.frequency_bundle import (
    build_dual_frequency_bundle,
    read_dual_frequency_bundle,
    write_dual_frequency_bundle,
)
from geodesy_preprocessing.sar.common.models import (
    BackendIdentity,
    GeometryType,
    RegisteredStackAsset,
    RegisteredStackManifest,
)
from geodesy_preprocessing.sar.common.stack_manifest import write_stack_manifest


class FrequencyBundleTests(unittest.TestCase):
    """Keep LA/LB independent while validating their scientific relationship."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _stack(
        self,
        name: str,
        frequency: str,
        *,
        aoi_digest: str = "a" * 64,
        buffer_km: float = 5.0,
        **changes,
    ) -> Path:
        root = self.root / name
        (root / "data").mkdir(parents=True)
        dates = changes.get("dates", ("20260105", "20260117"))
        polarizations = changes.get("polarizations", ("HH",))
        assets = []
        for date in dates:
            for polarization in polarizations:
                data = root / "data" / f"{date}.{polarization}.rslc"
                par = root / "data" / f"{date}.{polarization}.rslc.par"
                data.write_bytes(b"data")
                par.write_text("parameter\n")
                assets.append(
                    RegisteredStackAsset(date, polarization, data.relative_to(root), par.relative_to(root))
                )
        values = dict(
            schema_version=2,
            status="complete",
            sensor="nisar",
            source_product_type="RSLC",
            backend=BackendIdentity("gamma", "20261004"),
            geometry_type=GeometryType.RADAR_GRID,
            reference_date="20260105",
            dates=dates,
            frequencies=(frequency,),
            polarizations=polarizations,
            assets=tuple(assets),
            orbit_direction="DESCENDING",
            track="87",
            frame="22",
            pixel_spacing=(2.0, 3.0),
            shape=(100, 200),
            provenance_path=Path("crop_window.json"),
        )
        values.update(changes)
        (root / "crop_window.json").write_text(
            json.dumps({"aoi_sha256": aoi_digest, "buffer_km": buffer_km}),
            encoding="utf-8",
        )
        return write_stack_manifest(RegisteredStackManifest(**values), root / "registered_stack.json")

    def test_links_different_grids_and_orders_roles_by_real_frequency(self) -> None:
        main = self._stack("LA", "1.20e9", shape=(100, 200))
        auxiliary = self._stack("LB", "1.30e9", shape=(80, 150), pixel_spacing=(3.0, 4.0))

        bundle = build_dual_frequency_bundle(main, auxiliary, "a" * 64, 5.0)

        self.assertEqual(bundle.main_frequency_hz, 1.20e9)
        self.assertEqual(bundle.auxiliary_frequency_hz, 1.30e9)
        self.assertEqual((bundle.low_frequency_role, bundle.high_frequency_role), ("main", "auxiliary"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            bundle.buffer_km = 9.0  # type: ignore[misc]

    def test_rejects_relationship_mismatch_multiple_frequencies_and_equal_frequency(self) -> None:
        main = self._stack("LA", "1.20e9")
        mismatches = (
            ("sensor", "other"),
            ("track", "88"),
            ("frame", "23"),
            ("orbit_direction", "ASCENDING"),
            ("reference_date", "20260117"),
            ("dates", ("20260105",)),
            ("polarizations", ("HV",)),
        )
        for field, value in mismatches:
            with self.subTest(field=field):
                auxiliary = self._stack(f"LB_{field}", "1.30e9", **{field: value})
                with self.assertRaisesRegex(ValueError, field):
                    build_dual_frequency_bundle(main, auxiliary, "a" * 64, 5.0)
        multiple = self._stack("LB_multiple", "1.30e9", frequencies=("1.30e9", "1.31e9"))
        with self.assertRaisesRegex(ValueError, "exactly one frequency"):
            build_dual_frequency_bundle(main, multiple, "a" * 64, 5.0)
        equal = self._stack("LB_equal", "1.20e9")
        with self.assertRaisesRegex(ValueError, "different"):
            build_dual_frequency_bundle(main, equal, "a" * 64, 5.0)
        missing_identity = self._stack("LB_missing_identity", "1.30e9", track=None)
        with self.assertRaisesRegex(ValueError, "track"):
            build_dual_frequency_bundle(
                self._stack("LA_missing_identity", "1.20e9", track=None),
                missing_identity,
                "a" * 64,
                5.0,
            )

    def test_round_trip_uses_relative_paths_and_deterministic_json(self) -> None:
        main = self._stack("LA", "1.20e9", aoi_digest="b" * 64, buffer_km=7.5)
        auxiliary = self._stack("LB", "1.30e9", aoi_digest="b" * 64, buffer_km=7.5)
        bundle = build_dual_frequency_bundle(main, auxiliary, "b" * 64, 7.5)
        destination = self.root / "PRODUCT/NISAR_LA_LB/dual_frequency.json"

        first = write_dual_frequency_bundle(bundle, destination)
        first_bytes = first.read_bytes()
        loaded = read_dual_frequency_bundle(first)
        second = write_dual_frequency_bundle(loaded, destination.parent / "copy.json")

        self.assertEqual(loaded, bundle)
        self.assertEqual(second.read_bytes(), first_bytes)
        self.assertNotIn(str(self.root).encode(), first_bytes)

    def test_rejects_mismatched_or_unproven_crop_identity(self) -> None:
        main = self._stack("LA", "1.20e9")
        wrong_aoi = self._stack("LB_AOI", "1.30e9", aoi_digest="b" * 64)
        with self.assertRaisesRegex(ValueError, "AOI"):
            build_dual_frequency_bundle(main, wrong_aoi, "a" * 64, 5.0)

        wrong_buffer = self._stack("LB_BUFFER", "1.30e9", buffer_km=7.0)
        with self.assertRaisesRegex(ValueError, "buffer"):
            build_dual_frequency_bundle(main, wrong_buffer, "a" * 64, 5.0)

        (main.parent / "crop_window.json").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            build_dual_frequency_bundle(
                main,
                self._stack("LB_MISSING", "1.30e9"),
                "a" * 64,
                5.0,
            )

    def test_incomplete_lb_does_not_publish_bundle(self) -> None:
        main = self._stack("LA", "1.20e9")
        auxiliary = self._stack("LB", "1.30e9")
        (auxiliary.parent / "data/20260117.HH.rslc").unlink()
        destination = self.root / "dual_frequency.json"

        with self.assertRaises(FileNotFoundError):
            build_dual_frequency_bundle(main, auxiliary, "a" * 64, 5.0)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
