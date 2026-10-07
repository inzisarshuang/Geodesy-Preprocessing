"""Tests for the backend-neutral registered-stack manifest."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.common.models import (
    BackendIdentity,
    GeometryType,
    RegisteredStackAsset,
    RegisteredStackManifest,
)
from geodesy_preprocessing.sar.common.stack_manifest import (
    read_stack_manifest,
    write_stack_manifest,
)


class StackManifestTests(unittest.TestCase):
    """Validate immutable metadata and transactional JSON publication."""

    def test_typed_manifest_is_immutable(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest.geometry_type, GeometryType.RADAR_GRID)
        self.assertEqual(manifest.backend, BackendIdentity("gamma", "20251203"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            manifest.sensor = "changed"  # type: ignore[misc]
        for field, mutable_value in (
            ("dates", ["20240101"]),
            ("frequencies", ["C"]),
            ("polarizations", ["VV"]),
            ("assets", list(manifest.assets)),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(TypeError, field):
                    dataclasses.replace(manifest, **{field: mutable_value})

    def test_round_trip_is_deterministic_and_uses_sorted_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "data").mkdir()
            (root / "data/20240101.slc").touch()
            (root / "data/20240101.slc.par").touch()
            manifest = self._manifest()
            first_path = write_stack_manifest(manifest, root / "stack.json")
            first_bytes = first_path.read_bytes()
            loaded = read_stack_manifest(first_path)
            second_path = write_stack_manifest(loaded, root / "stack-copy.json")

            self.assertEqual(loaded, manifest)
            self.assertEqual(second_path.read_bytes(), first_bytes)
            keys = list(json.loads(first_bytes).keys())
            self.assertEqual(keys, sorted(keys))

    def test_rejects_invalid_or_duplicate_dates(self) -> None:
        for dates in (
            ("20240230",),
            ("20240101", "20240101"),
            ("2024-01-01",),
        ):
            with self.subTest(dates=dates):
                with self.assertRaisesRegex(ValueError, "date"):
                    self._manifest(dates=dates)

    def test_rejects_unsafe_backend_names(self) -> None:
        for name in ("GAMMA", "gamma backend", "../gamma", "gamma/backend", ""):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "backend name"):
                    BackendIdentity(name, "1")

    def test_rejects_missing_or_escaping_asset_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for data_path, parameter_path in (
                (Path("data/missing.slc"), Path("data/missing.slc.par")),
                (Path("../outside.slc"), Path("data/a.slc.par")),
                (Path("data/a.slc"), Path("/tmp/a.slc.par")),
            ):
                with self.subTest(data_path=data_path, parameter_path=parameter_path):
                    manifest = self._manifest(
                        assets=(
                            RegisteredStackAsset(
                                "20240101", "VV", data_path, parameter_path
                            ),
                        )
                    )
                    with self.assertRaisesRegex((FileNotFoundError, ValueError), "asset"):
                        write_stack_manifest(manifest, root / "stack.json")

    def test_rejects_duplicate_or_incomplete_asset_identity(self) -> None:
        vv = RegisteredStackAsset(
            "20240101", "VV", Path("data/a.rslc"), Path("data/a.rslc.par")
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._manifest(assets=(vv, vv))
        with self.assertRaisesRegex(ValueError, "polarization"):
            self._manifest(
                assets=(
                    RegisteredStackAsset(
                        "20240101", "HH", Path("data/a.rslc"), Path("data/a.rslc.par")
                    ),
                )
            )
        with self.assertRaisesRegex(ValueError, "complete"):
            self._manifest(
                dates=("20240101", "20240113"),
                polarizations=("VV", "VH"),
                assets=(
                    vv,
                    RegisteredStackAsset(
                        "20240101", "VH", Path("data/a.vh.rslc"), Path("data/a.vh.rslc.par")
                    ),
                    RegisteredStackAsset(
                        "20240113", "VV", Path("data/b.rslc"), Path("data/b.rslc.par")
                    ),
                ),
            )

    def test_atomic_replace_failure_preserves_existing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "data").mkdir()
            (root / "data/20240101.slc").touch()
            (root / "data/20240101.slc.par").touch()
            destination = root / "stack.json"
            destination.write_bytes(b"previous manifest\n")
            before = set(root.iterdir())

            with mock.patch(
                "geodesy_preprocessing.sar.common.stack_manifest.os.replace",
                side_effect=OSError("simulated replacement failure"),
            ):
                with self.assertRaisesRegex(OSError, "replacement failure"):
                    write_stack_manifest(
                        self._manifest(), destination
                    )

            self.assertEqual(destination.read_bytes(), b"previous manifest\n")
            self.assertEqual(set(root.iterdir()), before)

    @staticmethod
    def _manifest(
        *,
        dates: tuple[str, ...] = ("20240101",),
        polarizations: tuple[str, ...] = ("VV",),
        assets: tuple[RegisteredStackAsset, ...] | None = None,
    ) -> RegisteredStackManifest:
        if assets is None:
            assets = (
                RegisteredStackAsset(
                    dates[0],
                    polarizations[0],
                    Path("data/20240101.slc"),
                    Path("data/20240101.slc.par"),
                ),
            )
        return RegisteredStackManifest(
            schema_version=2,
            status="complete",
            sensor="sentinel1",
            source_product_type="SLC",
            backend=BackendIdentity("gamma", "20251203"),
            geometry_type=GeometryType.RADAR_GRID,
            reference_date=dates[0],
            dates=dates,
            frequencies=("C",),
            polarizations=polarizations,
            assets=assets,
        )


if __name__ == "__main__":
    unittest.main()
