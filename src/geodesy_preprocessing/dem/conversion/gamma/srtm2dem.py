"""GAMMA DEM conversion and validation.

功能概述:
    在隔离临时目录调用 ``srtm2dem``，校验 REAL*4 文件后成组发布结果。

函数说明:
    ``validate_gamma_dem`` 检查二进制 DEM 与参数文件；
    ``convert_with_srtm2dem`` 执行转换、校验并安全发布；
    ``run_gamma_dem_convert`` 处理预览、完整结果跳过和残缺结果保护。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import rasterio

from ...acquisition.opentopography import sha256_file
from .config import GammaDemConvertCase


def _parse_gamma_parameters(path: Path) -> dict[str, str]:
    parameters: dict[str, str] = {}
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            parameters[key.strip()] = value.strip()
    return parameters


def _positive_integer(parameters: dict[str, str], name: str) -> int:
    raw = parameters.get(name, "").split()
    if not raw:
        raise RuntimeError(f"GAMMA DEM parameter file is missing {name}")
    try:
        value = int(raw[0])
    except ValueError as error:
        raise RuntimeError(f"GAMMA DEM parameter {name} must be an integer") from error
    if value <= 0:
        raise RuntimeError(f"GAMMA DEM parameter {name} must be positive")
    return value


def validate_gamma_dem(dem_path: Path, par_path: Path) -> dict[str, object]:
    """Validate a GAMMA REAL*4 DEM against parameter-file dimensions."""

    dem_path, par_path = Path(dem_path), Path(par_path)
    if not dem_path.is_file() or not par_path.is_file():
        raise RuntimeError("srtm2dem did not create both DEM and DEM parameter files")
    parameters = _parse_gamma_parameters(par_path)
    width = _positive_integer(parameters, "width")
    nlines = _positive_integer(parameters, "nlines")
    data_format = parameters.get("data_format", "").split()
    data_format_value = data_format[0] if data_format else ""
    if data_format_value != "REAL*4":
        raise RuntimeError(
            f"srtm2dem output must use GAMMA REAL*4, got {data_format_value!r}"
        )
    expected_bytes = width * nlines * 4
    actual_bytes = dem_path.stat().st_size
    if actual_bytes != expected_bytes:
        raise RuntimeError(
            "GAMMA DEM size does not match its parameter file: "
            f"actual={actual_bytes}, expected={expected_bytes}, "
            f"shape=({nlines}, {width})"
        )
    projection = parameters.get(
        "DEM_projection", parameters.get("dem_projection", "unknown")
    ).split()[0]
    return {
        "dem_projection": projection,
        "data_format": data_format_value,
        "width": width,
        "nlines": nlines,
        "bytes": actual_bytes,
        "sha256": sha256_file(dem_path),
    }


def _prepare_gamma_geoid_geotiff(
    geoid_path: Path, input_geotiff: Path, output_path: Path
) -> Path:
    with rasterio.open(input_geotiff) as dem_dataset:
        dem_bounds = dem_dataset.bounds
    with rasterio.open(geoid_path) as geoid_dataset:
        x_margin = 2.0 * abs(float(geoid_dataset.transform.a))
        y_margin = 2.0 * abs(float(geoid_dataset.transform.e))
        requested = rasterio.windows.from_bounds(
            dem_bounds.left - x_margin,
            dem_bounds.bottom - y_margin,
            dem_bounds.right + x_margin,
            dem_bounds.top + y_margin,
            transform=geoid_dataset.transform,
        ).round_offsets().round_lengths()
        full = rasterio.windows.Window(
            0, 0, geoid_dataset.width, geoid_dataset.height
        )
        window = requested.intersection(full)
        values = geoid_dataset.read(1, window=window)
        if values.size == 0:
            raise RuntimeError("geoid grid does not overlap the downloaded DEM")
        profile = {
            "driver": "GTiff",
            "height": values.shape[0],
            "width": values.shape[1],
            "count": 1,
            "dtype": values.dtype,
            "crs": "EPSG:4326",
            "transform": geoid_dataset.window_transform(window),
        }
        with rasterio.open(output_path, "w", **profile) as output:
            output.write(values, 1)
            output.update_tags(AREA_OR_POINT="Point")
    return output_path


def _publish_group(
    sources: list[Path], destinations: list[Path], backup_root: Path
) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for index, destination in enumerate(destinations):
            if destination.exists():
                backup = backup_root / f"{index}.{destination.name}.backup"
                destination.replace(backup)
                backups.append((backup, destination))
        for source, destination in zip(sources, destinations, strict=True):
            source.replace(destination)
            published.append(destination)
    except BaseException:
        for destination in published:
            destination.unlink(missing_ok=True)
        for backup, destination in reversed(backups):
            backup.replace(destination)
        raise


def convert_with_srtm2dem(
    *,
    input_geotiff: Path,
    output_dem: Path,
    output_par: Path,
    command: str = "srtm2dem",
    geoid_correction_flag: int = 0,
    geoid_path: Path | None = None,
    gdal_bin_dir: Path | None = None,
    log_path: Path | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Run ``srtm2dem`` in isolation and publish a validated output group."""

    input_geotiff = Path(input_geotiff).expanduser().resolve()
    output_dem = Path(output_dem).expanduser().resolve()
    output_par = Path(output_par).expanduser().resolve()
    if not input_geotiff.is_file():
        raise FileNotFoundError(f"input GeoTIFF not found: {input_geotiff}")
    if output_dem.parent != output_par.parent:
        raise ValueError("srtm2dem DEM and parameter file must share one directory")
    if geoid_correction_flag not in {0, 1, 2, 3}:
        raise ValueError("srtm2dem geoid correction flag must be 0, 1, 2, or 3")
    if geoid_correction_flag == 2:
        if geoid_path is None:
            raise ValueError("srtm2dem geoid flag 2 requires a geoid file")
        geoid_path = Path(geoid_path).expanduser().resolve()
        if not geoid_path.is_file():
            raise FileNotFoundError(f"geoid file not found: {geoid_path}")

    executable = shutil.which(command)
    if executable is None:
        candidate = Path(command).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(f"GAMMA srtm2dem command not found: {command}")
        executable = str(candidate.resolve())

    destinations = [output_dem, output_par]
    if log_path is not None:
        log_path = Path(log_path).expanduser().resolve()
        if log_path.parent != output_dem.parent:
            raise ValueError("srtm2dem log must share the DEM output directory")
        destinations.append(log_path)
    existing = [str(path) for path in destinations if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "srtm2dem output already exists; enable overwrite to replace it: "
            + ", ".join(existing)
        )

    output_dem.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    if gdal_bin_dir is not None:
        gdal_bin_dir = Path(gdal_bin_dir).expanduser().resolve()
        if not (gdal_bin_dir / "gdalinfo").is_file():
            raise FileNotFoundError(f"gdalinfo not found in: {gdal_bin_dir}")
        environment["PATH"] = str(gdal_bin_dir) + os.pathsep + environment["PATH"]
        prefix = gdal_bin_dir.parent
        if (prefix / "lib").is_dir():
            current = environment.get("LD_LIBRARY_PATH", "")
            environment["LD_LIBRARY_PATH"] = str(prefix / "lib") + (
                os.pathsep + current if current else ""
            )
        if (prefix / "share" / "gdal").is_dir():
            environment["GDAL_DATA"] = str(prefix / "share" / "gdal")
        if (prefix / "share" / "proj").is_dir():
            environment["PROJ_DATA"] = str(prefix / "share" / "proj")

    with tempfile.TemporaryDirectory(
        prefix=".srtm2dem.", dir=output_dem.parent
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        temporary_dem = temporary_root / output_dem.name
        temporary_par = temporary_root / output_par.name
        geoid_argument = geoid_path
        if (
            geoid_correction_flag == 2
            and geoid_path is not None
            and geoid_path.suffix.lower() in {".tif", ".tiff"}
        ):
            geoid_argument = _prepare_gamma_geoid_geotiff(
                geoid_path,
                input_geotiff,
                temporary_root / "geoid_for_gamma.tif",
            )
        arguments = [
            executable,
            str(input_geotiff),
            str(temporary_dem),
            str(temporary_par),
            str(geoid_correction_flag),
        ]
        if geoid_correction_flag == 2:
            arguments.extend([str(geoid_argument), "-"])
        result = subprocess.run(
            arguments,
            cwd=temporary_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout)[-4000:]
            raise RuntimeError(
                f"GAMMA srtm2dem failed with exit code {result.returncode}:\n{detail}"
            )
        metadata = validate_gamma_dem(temporary_dem, temporary_par)
        sources = [temporary_dem, temporary_par]
        if log_path is not None:
            temporary_log = temporary_root / "srtm2dem.log"
            if not temporary_log.is_file():
                temporary_log.write_text(
                    result.stdout + result.stderr, encoding="utf-8"
                )
            sources.append(temporary_log)
        _publish_group(sources, destinations, temporary_root / "backups")

    metadata.update(
        {
            "dem_file": str(output_dem),
            "parameter_file": str(output_par),
            "log_file": str(log_path) if log_path is not None else None,
            "geoid_correction_flag": geoid_correction_flag,
            "geoid_file": str(geoid_path) if geoid_path is not None else None,
        }
    )
    return metadata


def run_gamma_dem_convert(
    case: GammaDemConvertCase, *, dry_run: bool = False
) -> dict[str, object] | None:
    """Run, preview, or safely skip one standalone GAMMA DEM conversion."""

    if not isinstance(case, GammaDemConvertCase):
        raise TypeError("case must be GammaDemConvertCase")
    paths = [case.input_geotiff, case.output_dem, case.output_par]
    paths.extend(
        path
        for path in (case.log_path, case.geoid_path, case.gdal_bin_dir)
        if path is not None
    )
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"symbolic link is not accepted for this task: {path}")
    if not case.input_geotiff.is_file():
        raise FileNotFoundError(f"input GeoTIFF not found: {case.input_geotiff}")
    if case.geoid_correction_flag == 2 and (
        case.geoid_path is None or not case.geoid_path.is_file()
    ):
        raise FileNotFoundError(f"geoid file not found: {case.geoid_path}")
    if case.output_dem.parent != case.output_par.parent:
        raise ValueError("GAMMA DEM and parameter file must share one output directory")
    if case.log_path is not None and case.log_path.parent != case.output_dem.parent:
        raise ValueError("GAMMA DEM log must share the DEM output directory")

    destinations = [case.output_dem, case.output_par]
    if case.log_path is not None:
        destinations.append(case.log_path)
    existing = [path for path in destinations if path.exists()]
    if existing and not case.overwrite:
        if len(existing) != len(destinations):
            raise RuntimeError(
                "incomplete GAMMA DEM output exists; inspect or explicitly enable overwrite: "
                + ", ".join(str(path) for path in existing)
            )
        metadata = validate_gamma_dem(case.output_dem, case.output_par)
        metadata["status"] = "skipped_complete"
        return metadata

    arguments = [
        case.command,
        str(case.input_geotiff),
        str(case.output_dem),
        str(case.output_par),
        str(case.geoid_correction_flag),
    ]
    if case.geoid_correction_flag == 2:
        arguments.extend([str(case.geoid_path), "-"])
    if dry_run:
        print(f"[GAMMA DEM] dry-run: {shlex.join(arguments)}")
        return None
    return convert_with_srtm2dem(
        input_geotiff=case.input_geotiff,
        output_dem=case.output_dem,
        output_par=case.output_par,
        command=case.command,
        geoid_correction_flag=case.geoid_correction_flag,
        geoid_path=case.geoid_path,
        gdal_bin_dir=case.gdal_bin_dir,
        log_path=case.log_path,
        overwrite=case.overwrite,
    )


__all__ = ["convert_with_srtm2dem", "run_gamma_dem_convert", "validate_gamma_dem"]
