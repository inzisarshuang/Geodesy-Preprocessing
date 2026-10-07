"""Small deterministic vendor fixtures used by Other-SAR tests."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path


def write_alos_zip(
    path: Path,
    *,
    date: str = "20260922",
    polarizations: tuple[str, ...] = ("HH",),
    metadata_name: str = "summary.txt",
    prefix: str = "SCENE",
    extra_members: tuple[tuple[str, bytes, int | None], ...] = (),
) -> Path:
    """Create a minimal CEOS-like archive with literal test payloads."""

    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = f'Img_SceneCenterDateTime = "{date}T101112"\n'.encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{prefix}/{metadata_name}", metadata)
        archive.writestr(f"{prefix}/LED-TEST", b"leader")
        for polarization in polarizations:
            archive.writestr(f"{prefix}/IMG-{polarization}-TEST", polarization.encode())
        for name, payload, mode in extra_members:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.create_system = 3
                info.external_attr = mode << 16
            archive.writestr(info, payload)
    return path


def alos_symlink_mode() -> int:
    """Return a Unix mode marking a ZIP entry as a symbolic link."""

    return stat.S_IFLNK | 0o777


__all__ = ["alos_symlink_mode", "write_alos_zip"]


def write_cosmo_product(
    raw_root: Path,
    *,
    sensor: str,
    date: str,
    scene_name: str | None = None,
) -> Path:
    """Create one nested CSG or CSKS HDF5 fixture with a validated name."""

    marker = sensor.upper()
    scene = raw_root / (scene_name or date) / "PRODUCT"
    scene.mkdir(parents=True, exist_ok=True)
    product = scene / f"{marker}1_SCS_B_HI_09_HH_RA_SF_{date}T101112_TEST.h5"
    product.write_bytes(b"hdf5-fixture")
    return product


__all__.append("write_cosmo_product")


def write_lt1_product(
    raw_root: Path, *, date: str, scene_name: str | None = None
) -> tuple[Path, Path]:
    """Create one structurally paired LT-1 TIFF and metadata XML fixture."""

    scene = raw_root / (scene_name or f"LT1_SCENE_{date}")
    scene.mkdir(parents=True, exist_ok=True)
    stem = f"LT1_SLC_{date}"
    image = scene / f"{stem}.tiff"
    metadata = scene / f"{stem}.meta.xml"
    image.write_bytes(b"tiff-fixture")
    metadata.write_text(f"<acquisition>{date}</acquisition>\n", encoding="utf-8")
    return image, metadata


__all__.append("write_lt1_product")


def write_rsat2_product(
    raw_root: Path,
    *,
    date: str,
    polarizations: tuple[str, ...] = ("HH",),
    scene_name: str | None = None,
) -> Path:
    """Create one minimal RADARSAT-2 product directory."""

    scene = raw_root / (scene_name or f"RS2_SCENE_{date}")
    scene.mkdir(parents=True, exist_ok=True)
    (scene / "product.xml").write_text(
        f"<product><rawDataStartTime>{date[:4]}-{date[4:6]}-{date[6:8]}T10:11:12Z</rawDataStartTime></product>\n",
        encoding="utf-8",
    )
    (scene / "lutSigma.xml").write_text("<lutSigma/>\n", encoding="utf-8")
    for polarization in polarizations:
        (scene / f"imagery_{polarization}.tif").write_bytes(polarization.encode())
    return scene


__all__.append("write_rsat2_product")


def write_saocom_product(
    raw_root: Path,
    *,
    date: str,
    pair_names: tuple[str, ...] = ("A",),
    scene_name: str | None = None,
) -> Path:
    """Create one SAOCOM Data directory with matched raster/XML stems."""

    scene = raw_root / (scene_name or f"SAOCOM_PRODUCT_{date}")
    data = scene / "Data"
    data.mkdir(parents=True, exist_ok=True)
    for name in reversed(pair_names):
        (data / f"{name}.xml").write_text(f"<date>{date}</date>\n", encoding="utf-8")
        (data / f"{name}.bin").write_bytes(f"data-{name}".encode())
    return scene


__all__.append("write_saocom_product")
