"""Safe planning for GAMMA TOPS reference DEM geocoding.

功能概述:
    在调用 GAMMA 前解析并校验参考日期、唯一 DEM、参考 SLC 来源、输出目录和
    几何编码控制参数；完整输出可跳过，不完整输出不会被自动删除。

函数说明:
    ``GammaTopsGeoRefPlan``（兼容名 ``GeoRefPlan``）保存计划；
    ``build_gamma_tops_geo_ref_plan``（兼容名 ``build_geo_ref_plan``）创建计划。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_REFERENCE_PATTERN = re.compile(r"^[0-9]{8}$")


@dataclass(frozen=True)
class GammaTopsGeoRefPlan:
    """Validated immutable inputs for one S1-04 reference scene."""

    reference: str
    dem_file: Path
    dem_par: Path
    slc_dir: Path
    slc_sources: tuple[Path, ...]
    output_dir: Path
    range_looks: int
    azimuth_looks: int
    latitude_oversampling: float | None
    longitude_oversampling: float | None
    reference_flag: int
    incidence_flag: int
    expected_outputs: tuple[Path, ...]
    skip: bool


GeoRefPlan = GammaTopsGeoRefPlan


def _directory(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")
    return resolved


def _one_file(directory: Path, pattern: str, label: str) -> Path:
    matches = tuple(
        path.resolve(strict=True)
        for path in sorted(directory.glob(pattern), key=lambda item: item.name)
        if path.is_file()
    )
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {label} in {directory}, found {len(matches)}"
        )
    return matches[0]


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _optional_positive(value: object, label: str) -> float | None:
    if value is None or value == "-":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be positive")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be positive") from error
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def build_gamma_tops_geo_ref_plan(
    reference: str,
    dem_dir: str | Path,
    slc_dir: str | Path,
    output_dir: str | Path,
    range_looks: int,
    azimuth_looks: int,
    latitude_oversampling: float | str | None = None,
    longitude_oversampling: float | str | None = None,
    reference_flag: int = 1,
    incidence_flag: int = 0,
) -> GeoRefPlan:
    """Validate S1-04 inputs without changing the filesystem."""

    reference_text = str(reference).strip()
    if not _REFERENCE_PATTERN.fullmatch(reference_text):
        raise ValueError("reference must be an eight-digit YYYYMMDD value")
    dem_root = _directory(dem_dir, "DEM directory")
    slc_root = _directory(slc_dir, "SLC directory")
    dem_file = _one_file(dem_root, "*.dem", "DEM data file")
    dem_par = _one_file(dem_root, "*dem.par", "DEM parameter file")

    scene = _directory(slc_root / reference_text, "reference SLC directory")
    sources = tuple(
        path.resolve(strict=True)
        for path in sorted(
            scene.glob(f"{reference_text}.vv.*"), key=lambda item: item.name
        )
        if path.is_file()
    )
    tab_name = f"{reference_text}.vv.SLC_tab"
    if not sources or tab_name not in {path.name for path in sources}:
        raise FileNotFoundError(f"reference SLC tab not found: {scene / tab_name}")

    latitude = _optional_positive(latitude_oversampling, "latitude oversampling")
    longitude = _optional_positive(longitude_oversampling, "longitude oversampling")
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude oversampling must be provided together")
    if reference_flag not in {0, 1}:
        raise ValueError("reference flag must be 0 or 1")
    if incidence_flag not in {0, 1}:
        raise ValueError("incidence flag must be 0 or 1")

    output = Path(output_dir).expanduser().resolve()
    expected = tuple(
        output / f"{reference_text}{suffix}"
        for suffix in (".lt_fine", ".utm.dem", ".utm.dem.par", ".mli.utm", ".hgt_sim")
    )
    complete = all(path.is_file() and path.stat().st_size > 0 for path in expected)
    if output.exists() and not output.is_dir():
        raise FileExistsError(f"DEM-geocoding destination is not a directory: {output}")
    if output.exists() and not complete:
        raise FileExistsError(
            f"incomplete DEM-geocoding destination exists and will not be removed: {output}"
        )

    return GeoRefPlan(
        reference=reference_text,
        dem_file=dem_file,
        dem_par=dem_par,
        slc_dir=slc_root,
        slc_sources=sources,
        output_dir=output,
        range_looks=_positive_integer(range_looks, "range looks"),
        azimuth_looks=_positive_integer(azimuth_looks, "azimuth looks"),
        latitude_oversampling=latitude,
        longitude_oversampling=longitude,
        reference_flag=reference_flag,
        incidence_flag=incidence_flag,
        expected_outputs=expected,
        skip=complete,
    )


build_geo_ref_plan = build_gamma_tops_geo_ref_plan


__all__ = ["GammaTopsGeoRefPlan", "GeoRefPlan", "build_gamma_tops_geo_ref_plan", "build_geo_ref_plan"]
