"""Staged GAMMA execution for TOPS reference DEM geocoding.

功能概述:
    在目标目录的同级临时目录中执行 S1-04 GAMMA 命令链，自动估算 DEM
    过采样参数，验证关键产品，并在成功后原子发布。

函数说明:
    ``GeoRefCommandError`` 表示外部命令失败；``GeoRefCommandResult`` 记录命令；
    ``GammaTopsGeoRefResult``（兼容名 ``GeoRefResult``）记录状态；
    ``estimate_oversampling`` 计算采样系数；``GeoRefRunner`` 执行命令链；
    ``run_gamma_tops_geo_ref``（兼容名 ``run_geo_ref``）管理发布。
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.sar.slc_generation.gamma.tops.view import (
    read_slc_tab_rows,
    stage_slc_tabs,
)

from .geo_ref_plan import GeoRefPlan


class GeoRefCommandError(RuntimeError):
    """Failure of one named external DEM-geocoding stage."""

    def __init__(self, stage: str, exit_code: int, command: Sequence[str]) -> None:
        self.stage = stage
        self.exit_code = exit_code
        self.command = tuple(command)
        super().__init__(f"{stage} command exited with code {exit_code}")


@dataclass(frozen=True)
class GeoRefCommandResult:
    """One completed external command."""

    stage: str
    command: tuple[str, ...]
    exit_code: int


@dataclass(frozen=True)
class GammaTopsGeoRefResult:
    """Publication result for one reference scene."""

    status: str
    output_dir: Path


GeoRefResult = GammaTopsGeoRefResult


def _parameter(path: Path, key: str) -> float:
    values: list[str] = []
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if fields and fields[0] == f"{key}:":
            if len(fields) < 2:
                raise ValueError(f"invalid {key} field in {path}")
            values.append(fields[1])
    if len(values) != 1:
        raise ValueError(f"expected one {key} field in {path}")
    try:
        value = float(values[0])
    except ValueError as error:
        raise ValueError(f"{key} must be numeric in {path}") from error
    if not math.isfinite(value):
        raise ValueError(f"{key} must be finite in {path}")
    return value


def _positive_integer_parameter(path: Path, key: str) -> int:
    value = _parameter(path, key)
    integer = int(value)
    if value != integer or integer < 1:
        raise ValueError(f"{key} must be a positive integer in {path}")
    return integer


def _legacy_bc(expression: str, scale: int) -> float:
    """Evaluate one numeric expression with the legacy ``bc -l`` scale."""

    executable = shutil.which("bc")
    if executable is None:
        raise FileNotFoundError("required command not found in PATH: bc")
    completed = subprocess.run(
        (executable, "-l"),
        input=f"scale={scale};{expression}\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"bc failed while estimating DEM oversampling: {completed.stderr.strip()}")
    try:
        return float(completed.stdout.strip())
    except ValueError as error:
        raise ValueError("bc returned invalid DEM oversampling output") from error


def _legacy_awk_number(value: float) -> float:
    """Round a calculated number like awk's default ``OFMT=%.6g``."""

    return float(f"{value:.6g}")


def estimate_oversampling(
    mli_parameter_file: str | Path, dem_parameter_file: str | Path
) -> tuple[float, float]:
    """Return three-decimal latitude/northing and longitude/easting factors."""

    mli = Path(mli_parameter_file)
    dem = Path(dem_parameter_file)
    range_spacing = _parameter(mli, "range_pixel_spacing")
    azimuth_spacing = _parameter(mli, "azimuth_pixel_spacing")
    legacy_pi = 3.1415926
    incidence_angle = _parameter(mli, "incidence_angle")
    incidence_radians = incidence_angle * legacy_pi / 180.0
    if range_spacing <= 0 or azimuth_spacing <= 0 or math.sin(incidence_radians) <= 0:
        raise ValueError("MLI spacing and incidence angle must produce positive spacing")
    ground_range_spacing = _legacy_bc(
        f"{range_spacing}/s({incidence_angle}*{legacy_pi}/180)", 3
    )
    text = dem.read_text(encoding="ascii")
    if any(line.split()[:1] == ["corner_lat:"] for line in text.splitlines()):
        center_latitude = _parameter(mli, "center_latitude")
        longitude_degree_metres = _legacy_bc(
            f"111700*c({center_latitude}*{legacy_pi}/180)", 3
        )
        latitude_resolution = _legacy_awk_number(
            abs(_parameter(dem, "post_lat")) * 111700.0
        )
        longitude_resolution = _legacy_awk_number(
            abs(_parameter(dem, "post_lon")) * longitude_degree_metres
        )
    elif any(line.split()[:1] == ["corner_north:"] for line in text.splitlines()):
        latitude_resolution = _legacy_awk_number(abs(_parameter(dem, "post_north")))
        longitude_resolution = _legacy_awk_number(abs(_parameter(dem, "post_east")))
    else:
        raise ValueError(f"unsupported DEM grid in {dem}")
    latitude_ratio = _legacy_bc(f"{latitude_resolution}/{azimuth_spacing}", 6)
    longitude_ratio = _legacy_bc(
        f"{longitude_resolution}/{ground_range_spacing}", 6
    )
    latitude = float(f"{latitude_ratio:.3f}")
    longitude = float(f"{longitude_ratio:.3f}")
    if latitude <= 0 or longitude <= 0:
        raise ValueError("estimated oversampling must be positive")
    return latitude, longitude


class GeoRefRunner:
    """Execute the fixed S1-04 GAMMA command sequence in one staging directory."""

    def __init__(self, *, python_bin: str | Path | None = None) -> None:
        self._python_bin = Path(python_bin or sys.executable)

    def _execute(
        self,
        stage: str,
        command: Sequence[str],
        cwd: Path,
        *,
        stdout_path: Path | None = None,
        allow_failure: bool = False,
    ) -> GeoRefCommandResult:
        executable = shutil.which(command[0])
        if executable is None:
            raise FileNotFoundError(f"required command not found in PATH: {command[0]}")
        resolved = (executable, *(str(value) for value in command[1:]))
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join(
            (str(self._python_bin.parent), environment.get("PATH", ""))
        )
        if stdout_path is None:
            completed = subprocess.run(resolved, cwd=cwd, env=environment, check=False)
        else:
            with stdout_path.open("w", encoding="utf-8") as stream:
                completed = subprocess.run(
                    resolved,
                    cwd=cwd,
                    env=environment,
                    check=False,
                    stdout=stream,
                )
        if completed.returncode != 0 and not allow_failure:
            raise GeoRefCommandError(stage, completed.returncode, resolved)
        return GeoRefCommandResult(stage, tuple(resolved), completed.returncode)

    def run_stages(
        self,
        plan: GeoRefPlan,
        staging_dir: str | Path,
        *,
        stage_sink: Callable[[str], None] | None = None,
    ) -> tuple[GeoRefCommandResult, ...]:
        """Execute all S1-04 stages and verify required products."""

        staging = Path(staging_dir).resolve(strict=True)
        if not staging.is_dir():
            raise ValueError(f"staging path must be a directory: {staging}")
        sink = stage_sink or (lambda stage: None)
        reference = plan.reference
        os.symlink(plan.dem_file, staging / "srtm.dem")
        os.symlink(plan.dem_par, staging / "srtm.dem.par")
        tab_file = next(
            source
            for source in plan.slc_sources
            if source.name == f"{reference}.vv.SLC_tab"
        )
        rows = read_slc_tab_rows(tab_file, tab_file.parent)
        stage_slc_tabs(((tab_file, rows),), staging)

        results: list[GeoRefCommandResult] = []

        def run(
            stage: str,
            command: Sequence[str],
            *,
            stdout: str | None = None,
            allow_failure: bool = False,
        ) -> None:
            sink(stage)
            results.append(
                self._execute(
                    stage,
                    command,
                    staging,
                    stdout_path=staging / stdout if stdout else None,
                    allow_failure=allow_failure,
                )
            )

        run(
            "mosaic",
            (
                "SLC_mosaic_ScanSAR",
                f"{reference}.vv.SLC_tab",
                f"{reference}.slc",
                f"{reference}.slc.par",
                str(plan.range_looks),
                str(plan.azimuth_looks),
                "1",
            ),
        )
        run(
            "multi_look",
            (
                "multi_look",
                f"{reference}.slc",
                f"{reference}.slc.par",
                f"{reference}.mli",
                f"{reference}.mli.par",
                str(plan.range_looks),
                str(plan.azimuth_looks),
            ),
        )
        width = _positive_integer_parameter(staging / f"{reference}.mli.par", "range_samples")
        run("preview", ("raspwr", f"{reference}.mli", str(width)))

        if plan.latitude_oversampling is None:
            latitude, longitude = estimate_oversampling(
                staging / f"{reference}.mli.par", staging / "srtm.dem.par"
            )
        else:
            latitude = plan.latitude_oversampling
            longitude = plan.longitude_oversampling
        assert latitude is not None and longitude is not None
        (staging / "lat_lon_ovr").write_text(
            f"lat_ovr: {latitude:.3f}\nlon_ovr: {longitude:.3f}\n",
            encoding="ascii",
        )

        run(
            "gc_map2",
            (
                "gc_map2",
                f"{reference}.mli.par",
                "srtm.dem.par",
                "srtm.dem",
                f"{reference}.utm.dem.par",
                f"{reference}.utm.dem",
                f"{reference}.lt",
                str(latitude),
                str(longitude),
                f"{reference}.ls_map",
                "-",
                f"{reference}.inc",
                "-",
                "-",
                f"{reference}.sim_sar",
                f"{reference}.u",
                f"{reference}.v",
                f"{reference}.psi",
                f"{reference}.pix",
                "-", "-", "-", "-", "-", "-",
                str(plan.reference_flag),
                str(plan.incidence_flag),
            ),
        )
        coarse = tuple(
            staging / f"{reference}{suffix}"
            for suffix in (".utm.dem.par", ".utm.dem", ".lt", ".ls_map", ".inc")
        )
        if any(not path.is_file() or path.stat().st_size == 0 for path in coarse):
            raise FileNotFoundError("gc_map2 did not create all required coarse products")

        mli_par = staging / f"{reference}.mli.par"
        dem_par = staging / f"{reference}.utm.dem.par"
        width1 = _positive_integer_parameter(mli_par, "range_samples")
        lines1 = _positive_integer_parameter(mli_par, "azimuth_lines")
        width2 = _positive_integer_parameter(dem_par, "width")
        lines2 = _positive_integer_parameter(dem_par, "nlines")
        run("pixel_area", ("pixel_area", f"{reference}.mli.par", f"{reference}.utm.dem.par", f"{reference}.utm.dem", f"{reference}.lt", f"{reference}.ls_map", f"{reference}.inc", f"{reference}.pix_sigma0", f"{reference}.pix_gamma0"))
        run("create_diff_par", ("create_diff_par", f"{reference}.mli.par", "-", f"{reference}.diff_par", "1", "0"))
        run("init_offsetm", ("init_offsetm", f"{reference}.mli", f"{reference}.pix_sigma0", f"{reference}.diff_par", "1", "1"))
        run("offset_pwrm", ("offset_pwrm", f"{reference}.mli", f"{reference}.pix_sigma0", f"{reference}.diff_par", f"{reference}.offs", f"{reference}.snr", "256", "256", f"{reference}.offsets", "1", "32", "32", "0.1"))
        run("offset_fitm", ("offset_fitm", f"{reference}.offs", f"{reference}.snr", f"{reference}.diff_par", f"{reference}.coffs", f"{reference}.coffsets", "0.5", "3"), stdout=f"{reference}.offset_fit")
        run("gc_map_fine", ("gc_map_fine", f"{reference}.lt", str(width2), f"{reference}.diff_par", f"{reference}.lt_fine", "0"))
        run("geocode_back", ("geocode_back", f"{reference}.mli", str(width1), f"{reference}.lt_fine", f"{reference}.mli.utm", str(width2), str(lines2), "5", "0"))
        preview = ("raspwr", f"{reference}.mli.utm", str(width2), "1", "0", "1", "1", "1.", ".35", "1")
        run("preview_utm_bmp", (*preview, f"{reference}.mli.utm.bmp"))
        run("preview_utm_tif", (*preview, f"{reference}.mli.utm.tif"), allow_failure=True)
        run("geocode", ("geocode", f"{reference}.lt_fine", f"{reference}.utm.dem", str(width2), f"{reference}.hgt_sim", str(width1), str(lines1), "2", "0"))
        run("preview_height", ("rasdt_pwr", f"{reference}.hgt_sim", f"{reference}.mli", str(width1), "-", "-", "-", "-", "-", "-", "2", "rmg.cm", f"{reference}.hgt_sim.bmp"))

        missing = [
            path.name
            for path in plan.expected_outputs
            if not (staging / path.name).is_file()
            or (staging / path.name).stat().st_size == 0
        ]
        if missing:
            raise FileNotFoundError(
                f"DEM geocoding completed without required products: {', '.join(missing)}"
            )
        return tuple(results)


DEFAULT_GAMMA_TOPS_GEO_REF_TASK = "PRE_GAMMA_SAR_TOPS_GEO_REF"


def run_gamma_tops_geo_ref(
    plan: GeoRefPlan,
    *,
    task: str = DEFAULT_GAMMA_TOPS_GEO_REF_TASK,
    progress_sink: Callable[[str], None] = print,
    runner: GeoRefRunner | None = None,
) -> GeoRefResult:
    """Execute or skip one plan, publishing only a verified staging directory."""

    current_stage = "discover"

    def send(event: str, status: str, stage: str) -> None:
        progress_sink(
            emit_progress(
                event=event,
                task=task,
                item=plan.reference,
                index=1,
                total=1,
                stage=stage,
                status=status,
            )
        )

    send("item_started", "running", current_stage)
    if plan.skip:
        send("item_finished", "skipped", current_stage)
        return GeoRefResult("skipped", plan.output_dir)

    plan.output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{plan.output_dir.name}.",
            suffix=".stage",
            dir=plan.output_dir.parent,
        )
    )

    def stage_started(stage: str) -> None:
        nonlocal current_stage
        current_stage = stage
        send("stage_started", "running", stage)

    try:
        (runner or GeoRefRunner()).run_stages(plan, staging, stage_sink=stage_started)
        stage_started("publish")
        if plan.output_dir.exists():
            raise FileExistsError(
                f"DEM-geocoding destination appeared during execution: {plan.output_dir}"
            )
        os.replace(staging, plan.output_dir)
        send("item_finished", "success", "publish")
        return GeoRefResult("success", plan.output_dir)
    except BaseException:
        send("item_finished", "failed", current_stage)
        if staging.exists():
            shutil.rmtree(staging)
        raise


run_geo_ref = run_gamma_tops_geo_ref


__all__ = [
    "DEFAULT_GAMMA_TOPS_GEO_REF_TASK",
    "GammaTopsGeoRefResult",
    "GeoRefCommandError",
    "GeoRefCommandResult",
    "GeoRefResult",
    "GeoRefRunner",
    "estimate_oversampling",
    "run_gamma_tops_geo_ref",
    "run_geo_ref",
]
