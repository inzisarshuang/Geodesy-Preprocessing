"""Tests for read-only GAMMA registered-stack crop planning."""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.common.models import (
    BackendIdentity,
    GeometryType,
    RegisteredStackAsset,
    RegisteredStackManifest,
)
from geodesy_preprocessing.sar.common.stack_manifest import write_stack_manifest
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.config import (
    load_crop_config,
)
from geodesy_preprocessing.sar.rslc_subsetting.gamma.radar_grid.plan import build_crop_plan


class GammaCropPlanTests(unittest.TestCase):
    """Reject incomplete, inconsistent or dangerous crop inputs before GAMMA runs."""

    def _project(
        self,
        root: Path,
        *,
        polarizations: tuple[str, ...] = ("VV", "VH"),
    ) -> tuple[Path, Path]:
        project = root / "study"
        stack = project / "RAW/RSLC"
        data = stack / "RSLC"
        geometry = project / "RAW/GEOMETRY"
        config_dir = project / "config/preprocessing"
        aoi_dir = project / "AOI"
        for directory in (data, geometry, config_dir, aoi_dir):
            directory.mkdir(parents=True)
        dates = ("20240101", "20240113")
        assets: list[RegisteredStackAsset] = []
        for date in dates:
            for polarization in polarizations:
                stem = f"{date}.{polarization}"
                (data / f"{stem}.rslc").write_bytes(b"complex")
                (data / f"{stem}.rslc.par").write_text(
                    f"""date: {date[:4]} {date[4:6]} {date[6:8]} 00 00 00.0
range_samples: 8
azimuth_lines: 6
range_looks: 1
azimuth_looks: 1
image_format: FCOMPLEX
polarization: {polarization}
""",
                    encoding="ascii",
                )
                assets.append(
                    RegisteredStackAsset(
                        date,
                        polarization,
                        Path("RSLC") / f"{stem}.rslc",
                        Path("RSLC") / f"{stem}.rslc.par",
                    )
                )
        manifest = RegisteredStackManifest(
            schema_version=2,
            status="complete",
            sensor="sentinel1",
            source_product_type="SLC",
            backend=BackendIdentity("gamma", "20251203"),
            geometry_type=GeometryType.RADAR_GRID,
            reference_date="20240101",
            dates=dates,
            frequencies=("C",),
            polarizations=polarizations,
            assets=tuple(assets),
        )
        manifest_path = write_stack_manifest(manifest, stack / "registered_stack.json")
        (geometry / "20240101.utm.dem.par").write_text(
            """DEM_projection: EQA
EPSG: 4326
width: 4
nlines: 3
corner_lat: 40.0
corner_lon: -5.0
post_lat: -0.01
post_lon: 0.02
""",
            encoding="ascii",
        )
        (geometry / "20240101.mli.par").write_text(
            """date: 2024 01 01 00 00 00.0
range_samples: 4
azimuth_lines: 3
range_looks: 2
azimuth_looks: 2
image_format: FLOAT
polarization: VV
""",
            encoding="ascii",
        )
        (geometry / "20240101.lt_fine").write_bytes(b"\0" * (4 * 3 * 8))
        (aoi_dir / "main.kml").write_text(
            """<kml><Placemark><name>target</name><Polygon><outerBoundaryIs>
<LinearRing><coordinates>-5,39.98 -4.94,39.98 -4.94,40 -5,40 -5,39.98</coordinates>
</LinearRing></outerBoundaryIs></Polygon></Placemark></kml>""",
            encoding="utf-8",
        )
        config = config_dir / "gamma_sar_rslc_crop.cfg"
        config.write_text(
            """[project]
root = ../..
[defaults]
buffer_km = 5
polarizations = VV
require_full_aoi_coverage = true
min_buffer_coverage = 0.95
[case:main]
registered_stack = RAW/RSLC/registered_stack.json
geometry_dir = RAW/GEOMETRY
kml_file = AOI/main.kml
kml_placemark = target
output_dir = RAW/RSLC_CROP
""",
            encoding="utf-8",
        )
        return config, manifest_path

    def test_builds_read_only_plan_for_selected_polarization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path, _ = self._project(Path(directory))
            config = load_crop_config(config_path, "main")
            plan = build_crop_plan(config)

        self.assertEqual(plan.selected_polarizations, ("VV",))
        self.assertEqual(len(plan.assets), 2)
        self.assertEqual((plan.rslc_grid.width, plan.rslc_grid.nlines), (8, 6))
        self.assertEqual((plan.mli_grid.width, plan.mli_grid.nlines), (4, 3))
        self.assertEqual(plan.map_grid.crs.to_epsg(), 4326)
        self.assertGreater(plan.buffered_roi.area, plan.roi.area)
        self.assertFalse(plan.output_dir.exists())

    def test_all_selects_manifest_order_and_missing_polarization_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path, _ = self._project(Path(directory))
            config = load_crop_config(config_path, "main")
            all_plan = build_crop_plan(dataclasses.replace(config, polarizations=None))
            self.assertEqual(all_plan.selected_polarizations, ("VV", "VH"))
            with self.assertRaisesRegex(ValueError, "HH"):
                build_crop_plan(dataclasses.replace(config, polarizations=("HH",)))

    def test_rejects_missing_geometry_reference_mismatch_and_grid_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path, _ = self._project(Path(directory))
            config = load_crop_config(config_path, "main")
            lookup = config.geometry_dir / "20240101.lt_fine"
            lookup.unlink()
            with self.assertRaisesRegex(FileNotFoundError, "lt_fine"):
                build_crop_plan(config)
            lookup.write_bytes(b"\0" * (4 * 3 * 8))

            mli = config.geometry_dir / "20240101.mli.par"
            mli.write_text(mli.read_text().replace("2024 01 01", "2024 01 13"))
            with self.assertRaisesRegex(ValueError, "reference"):
                build_crop_plan(config)
            mli.write_text(mli.read_text().replace("2024 01 13", "2024 01 01"))

            parameter = config.registered_stack.parent / "RSLC/20240113.VV.rslc.par"
            parameter.write_text(parameter.read_text().replace("range_samples: 8", "range_samples: 9"))
            with self.assertRaisesRegex(ValueError, "grid"):
                build_crop_plan(config)

    def test_rejects_output_inside_source_and_existing_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path, _ = self._project(Path(directory))
            config = load_crop_config(config_path, "main")
            source = config.registered_stack.parent
            with self.assertRaisesRegex(ValueError, "inside"):
                build_crop_plan(dataclasses.replace(config, output_dir=source / "CROP"))

            config.output_dir.mkdir()
            (config.output_dir / "unexpected.txt").write_text("conflict")
            with self.assertRaisesRegex(FileExistsError, "incomplete|conflict"):
                build_crop_plan(config)
            forced_plan = build_crop_plan(config, allow_incomplete_output=True)
            self.assertFalse(forced_plan.existing_complete)


if __name__ == "__main__":
    unittest.main()
