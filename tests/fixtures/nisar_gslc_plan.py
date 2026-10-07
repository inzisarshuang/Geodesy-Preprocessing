"""Reusable tiny GSLC stack plans for publication and CLI tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from shapely.geometry import box

from geodesy_preprocessing.sar.acquisition.nisar.gslc_hdf5 import inspect_nisar_gslc
from geodesy_preprocessing.sar.gslc_subsetting.nisar.models import (
    GslcPixelWindow,
    NisarGslcAssetPlan,
    NisarGslcStackCase,
    NisarGslcStackPlan,
)
from tests.fixtures.nisar_gslc import write_gslc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_gslc_plan(
    root: Path,
    *,
    dates: tuple[str, ...] = ("20260105", "20260117"),
    polarizations: tuple[str, ...] = ("HH",),
) -> NisarGslcStackPlan:
    """Create a complete in-memory plan backed by tiny immutable HDF5 files."""

    project = root / "project"
    source_dir = project / "INPUT/NISAR/GSLC"
    source_dir.mkdir(parents=True)
    source_manifest = source_dir / "source_manifest.json"
    source_manifest.write_text('{"schema_version":1,"products":[]}\n', encoding="utf-8")
    kml = project / "INPUT/ROI/area.kml"
    kml.parent.mkdir(parents=True)
    kml.write_text("<kml>fixture</kml>\n", encoding="utf-8")
    case = NisarGslcStackCase(
        name="la",
        project_root=project,
        source_manifest=source_manifest,
        kml_file=kml,
        kml_placemark=None,
        buffer_km=0.0,
        band="L",
        frequency="A",
        polarizations=polarizations,
        reference_date=dates[0],
        output_dir=project / "WORK/NISAR/GSLC/LA/STACK",
    )
    window = GslcPixelWindow(0, 3, 1, 4)
    assets = []
    common_grid = None
    for date in dates:
        iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        source = source_dir / f"NISAR_GSLC_{date}.h5"
        write_gslc(
            source,
            bands={"L": {"A": polarizations}},
            granule_id=f"NISAR_L2_GSLC_{date}",
            acquisition_start=f"{iso}T10:20:30Z",
            acquisition_end=f"{iso}T10:21:02Z",
        )
        inventory = inspect_nisar_gslc(source)
        common_grid = inventory.grids[0]
        channels = {channel.polarization: channel for channel in inventory.channels}
        for polarization in polarizations:
            assets.append(
                NisarGslcAssetPlan(
                    date=date,
                    polarization=polarization,
                    source_file=source,
                    source_sha256=_sha256(source),
                    channel=channels[polarization],
                    grid=common_grid,
                    window=window,
                )
            )
    assert common_grid is not None
    roi = box(-3.01, 37.9, -2.99, 37.92)
    return NisarGslcStackPlan(
        case=case,
        product_type="GSLC",
        product_specification_version="1.4.0",
        track=87,
        frame=22,
        orbit_direction="ASCENDING",
        dates=dates,
        reference_date=dates[0],
        selected_polarizations=polarizations,
        grid=common_grid,
        window=window,
        roi=roi,
        buffered_roi=roi,
        roi_coverage=tuple((date, 1.0) for date in dates),
        buffered_coverage=tuple((date, 1.0) for date in dates),
        assets=tuple(assets),
        existing_complete=False,
    )


__all__ = ["make_gslc_plan"]
