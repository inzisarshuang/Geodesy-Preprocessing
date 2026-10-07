"""Safe planning for GAMMA TOPS stack coregistration.

功能概述:
    在运行 GAMMA 前读取影像列表，校验参考高程、每景三列 SLC tab 及其只读
    来源，并确定完整跳过或拒绝不完整目标目录。

函数说明:
    ``CoregItemPlan`` 描述日期；``GammaTopsCoregPlan``（兼容名 ``CoregPlan``）
    描述完整栈；``build_gamma_tops_coreg_plan``（兼容名 ``build_coreg_plan``）构建计划。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.tops.plan import read_image_items
from geodesy_preprocessing.sar.slc_generation.gamma.tops.view import read_slc_tab_rows


_DATE = re.compile(r"^[0-9]{8}$")
_BRIDGE_MODES = {"none", "yearly", "adjacent", "mix"}
_ID_MODES = {"date", "date_time", "date_pol", "date_time_pol"}


@dataclass(frozen=True)
class CoregItemPlan:
    """One ordered SLC scene and its validated tab sources."""

    date: str
    tab_file: Path
    tab_rows: tuple[tuple[Path, Path, Path], ...]
    sources: tuple[Path, ...]


@dataclass(frozen=True)
class GammaTopsCoregPlan:
    """Validated immutable inputs and settings for one S1-05 stack."""

    slc_dir: Path
    dem_geocode_dir: Path
    output_dir: Path
    reference: str
    images_file: Path
    height_file: Path
    range_looks: int
    azimuth_looks: int
    coherence_threshold: float
    fraction_threshold: float
    phase_stdev_threshold: float
    id_mode: str
    npoly: int
    bridge_mode: str
    items: tuple[CoregItemPlan, ...]
    expected_outputs: tuple[Path, ...]
    skip: bool


CoregPlan = GammaTopsCoregPlan


def _directory(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")
    return resolved


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _positive_float(value: object, label: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be positive")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be positive") from error
    if parsed <= 0 or (maximum is not None and parsed > maximum):
        limit = f" and at most {maximum}" if maximum is not None else ""
        raise ValueError(f"{label} must be positive{limit}")
    return parsed


def _read_tab(scene: Path, date: str) -> CoregItemPlan:
    tab = (scene / f"{date}.vv.SLC_tab").resolve(strict=True)
    rows = read_slc_tab_rows(tab, scene)
    sources: list[Path] = []
    seen: set[Path] = set()
    for row in rows:
        for source in row:
            if source not in seen:
                seen.add(source)
                sources.append(source)
    return CoregItemPlan(date, tab, rows, tuple(sources))


def build_gamma_tops_coreg_plan(
    slc_dir: str | Path,
    dem_geocode_dir: str | Path,
    output_dir: str | Path,
    reference: str,
    images_file: str | Path,
    range_looks: int,
    azimuth_looks: int,
    *,
    coherence_threshold: float = 0.8,
    fraction_threshold: float = 0.01,
    phase_stdev_threshold: float = 0.8,
    id_mode: str = "date",
    npoly: int = 4,
    bridge_mode: str = "none",
) -> CoregPlan:
    """Validate all S1-05 inputs without changing the source or destination."""

    slc_root = _directory(slc_dir, "SLC directory")
    dem_root = _directory(dem_geocode_dir, "DEM-geocoding directory")
    images = Path(images_file).expanduser().resolve(strict=True)
    dates = read_image_items(images)
    if any(not _DATE.fullmatch(date) for date in dates):
        raise ValueError("every image item must be an eight-digit YYYYMMDD value")
    reference_text = str(reference).strip()
    if reference_text not in dates:
        raise ValueError(f"reference {reference_text!r} is not present in image list")
    height = (dem_root / f"{reference_text}.hgt_sim").resolve(strict=True)
    if not height.is_file() or height.stat().st_size == 0:
        raise ValueError(f"reference height file must be non-empty: {height}")
    if bridge_mode not in _BRIDGE_MODES:
        raise ValueError(f"bridge mode must be one of: {', '.join(sorted(_BRIDGE_MODES))}")
    if id_mode not in _ID_MODES:
        raise ValueError(f"ID mode must be one of: {', '.join(sorted(_ID_MODES))}")
    if npoly not in {1, 3, 4, 6}:
        raise ValueError("npoly must be one of 1, 3, 4, or 6")
    items = tuple(_read_tab(_directory(slc_root / date, f"SLC scene {date}"), date) for date in dates)

    output = Path(output_dir).expanduser().resolve()
    expected: list[Path] = []
    for date in dates:
        expected.extend(
            (output / "RSLC" / f"{date}.rslc", output / "RSLC" / f"{date}.rslc.par")
        )
    expected.append(output / "coreg_quality" / "1ID.coreg_quality")
    complete = all(path.is_file() and path.stat().st_size > 0 for path in expected)
    if output.exists() and not output.is_dir():
        raise FileExistsError(f"RSLC destination is not a directory: {output}")
    if output.exists() and not complete:
        raise FileExistsError(
            f"incomplete RSLC destination exists and will not be removed: {output}"
        )

    return CoregPlan(
        slc_dir=slc_root,
        dem_geocode_dir=dem_root,
        output_dir=output,
        reference=reference_text,
        images_file=images,
        height_file=height,
        range_looks=_positive_integer(range_looks, "range looks"),
        azimuth_looks=_positive_integer(azimuth_looks, "azimuth looks"),
        coherence_threshold=_positive_float(coherence_threshold, "coherence threshold", maximum=1.0),
        fraction_threshold=_positive_float(fraction_threshold, "fraction threshold", maximum=1.0),
        phase_stdev_threshold=_positive_float(phase_stdev_threshold, "phase standard-deviation threshold"),
        id_mode=id_mode,
        npoly=npoly,
        bridge_mode=bridge_mode,
        items=items,
        expected_outputs=tuple(expected),
        skip=complete,
    )


build_coreg_plan = build_gamma_tops_coreg_plan


__all__ = ["CoregItemPlan", "GammaTopsCoregPlan", "CoregPlan", "build_gamma_tops_coreg_plan", "build_coreg_plan"]
