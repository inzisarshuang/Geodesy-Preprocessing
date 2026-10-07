"""GAMMA 普通雷达坐标参考几何的事务式执行器。

功能概述:
    在目标目录同级的暂存目录内依次执行 ``multi_look`` 和 ``geocoding.py``，
    校验 WGS84 雷达坐标高程等关键成果后一次性发布完整目录。

函数说明:
    ``GammaRadarGeoRefCommandError`` 描述外部命令失败；
    ``GammaRadarGeoRefResult`` 描述执行或跳过结果；
    ``run_gamma_radar_geo_ref`` 执行、记录溯源并原子发布参考几何。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from geodesy_preprocessing.common.progress import emit_progress

from .geo_ref_plan import GammaRadarGeoRefPlan


class GammaRadarGeoRefCommandError(RuntimeError):
    """Failure of one named external reference-geometry command."""

    def __init__(
        self, stage: str, exit_code: int, command: Sequence[str], stderr_log: Path
    ) -> None:
        self.stage = stage
        self.exit_code = exit_code
        self.command = tuple(command)
        self.stderr_log = stderr_log
        super().__init__(
            f"{stage} command exited with code {exit_code}; see {stderr_log}"
        )


@dataclass(frozen=True)
class GammaRadarGeoRefResult:
    """Publication status and destination for one reference geometry."""

    status: str
    output_dir: Path
    manifest: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _input_record(path: Path) -> dict[str, object]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": _sha256(path)}


def _input_records(plan: GammaRadarGeoRefPlan) -> dict[str, dict[str, object]]:
    return {
        "slc": _input_record(plan.reference_asset.data_path),
        "slc_parameter": _input_record(plan.reference_asset.parameter_path),
        "dem": _input_record(plan.dem),
        "dem_parameter": _input_record(plan.dem_par),
        "images": _input_record(plan.case.images_file.resolve(strict=True)),
    }


def _input_signature(
    plan: GammaRadarGeoRefPlan, records: dict[str, dict[str, object]]
) -> str:
    case = plan.case
    payload = {
        "inputs": records,
        "reference_date": case.reference_date,
        "primary_polarization": case.primary_polarization,
        "range_looks": case.range_looks,
        "azimuth_looks": case.azimuth_looks,
        "latitude_oversampling": case.latitude_oversampling,
        "longitude_oversampling": case.longitude_oversampling,
        "post_spacing": case.post_spacing,
        "correlation_threshold": case.correlation_threshold,
        "polynomial_parameters": case.polynomial_parameters,
        "range_offset_estimates": case.range_offset_estimates,
        "azimuth_offset_estimates": case.azimuth_offset_estimates,
        "range_window": case.range_window,
        "azimuth_window": case.azimuth_window,
        "area_factor": case.area_factor,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _preserve_failure_logs(staging: Path, output: Path) -> Path | None:
    logs = tuple(path for path in staging.rglob("*.log") if path.is_file())
    if not logs:
        return None
    destination = output.parent / f"{output.name}.failed-logs-{uuid.uuid4().hex[:8]}"
    for source in logs:
        target = destination / source.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def _staged(path: Path, output: Path, staging: Path) -> Path:
    return staging / path.relative_to(output)


def _recorded_command(command: Sequence[str], staging: Path) -> list[str]:
    prefix = str(staging)
    result: list[str] = []
    for value in command:
        result.append("$OUTPUT" + value[len(prefix) :] if value.startswith(prefix) else value)
    return result


def _run_command(
    stage: str,
    command: Sequence[str],
    *,
    staging: Path,
    environment: Mapping[str, str],
) -> tuple[str, ...]:
    executable = shutil.which(command[0], path=environment.get("PATH"))
    if executable is None:
        raise FileNotFoundError(f"required command not found in PATH: {command[0]}")
    resolved = (executable, *(str(value) for value in command[1:]))
    stdout_log = staging / f"{stage}.stdout.log"
    stderr_log = staging / f"{stage}.stderr.log"
    with stdout_log.open("w", encoding="utf-8") as stdout, stderr_log.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            resolved,
            cwd=staging,
            env=dict(environment),
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
            shell=False,
        )
    if completed.returncode != 0:
        raise GammaRadarGeoRefCommandError(
            stage, completed.returncode, resolved, stderr_log
        )
    return tuple(resolved)


def _geocoding_command(plan: GammaRadarGeoRefPlan, staging: Path) -> tuple[str, ...]:
    case = plan.case
    command: list[str] = [
        "geocoding.py",
        str(_staged(plan.reference_mli, case.output_dir, staging)),
        str(_staged(plan.reference_mli_par, case.output_dir, staging)),
        str(plan.dem),
        str(plan.dem_par),
        plan.root_name,
        "--summary",
        str(_staged(plan.geocoding_quality, case.output_dir, staging)),
        "--thres",
        str(case.correlation_threshold),
        "--npoly",
        str(case.polynomial_parameters),
        "--rwin",
        str(case.range_window),
        "--azwin",
        str(case.azimuth_window),
        "--area_fact",
        str(case.area_factor),
        "--out_dir",
        str(staging),
    ]
    if case.post_spacing is not None:
        command.extend(("--post", str(case.post_spacing)))
    elif case.latitude_oversampling is not None:
        assert case.longitude_oversampling is not None
        command.extend(
            (
                "--lat_ovr",
                str(case.latitude_oversampling),
                "--lon_ovr",
                str(case.longitude_oversampling),
            )
        )
    if case.range_offset_estimates is not None:
        command.extend(("--nr", str(case.range_offset_estimates)))
    if case.azimuth_offset_estimates is not None:
        command.extend(("--naz", str(case.azimuth_offset_estimates)))
    return tuple(command)


def run_gamma_radar_geo_ref(
    plan: GammaRadarGeoRefPlan,
    *,
    environment: Mapping[str, str] | None = None,
    progress_sink: Callable[[str], None] = print,
) -> GammaRadarGeoRefResult:
    """Run two GAMMA stages in isolation and atomically publish valid output."""

    task = "PRE_GAMMA_SAR_RADAR_GEO_REF"
    item = plan.root_name
    input_records = _input_records(plan)
    signature = _input_signature(plan, input_records)
    if plan.skip:
        try:
            existing = json.loads(plan.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FileExistsError(
                f"reference-geometry destination has invalid provenance: {plan.case.output_dir}"
            ) from error
        if existing.get("input_signature") != signature:
            raise FileExistsError(
                "reference-geometry destination was produced from different inputs"
            )
        progress_sink(
            emit_progress(
                event="item_finished",
                task=task,
                item=item,
                index=1,
                total=1,
                stage="publish",
                status="skipped",
            )
        )
        return GammaRadarGeoRefResult("skipped", plan.case.output_dir, plan.manifest)

    run_environment = os.environ.copy()
    if environment:
        run_environment.update((str(key), str(value)) for key, value in environment.items())
    output = plan.case.output_dir
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}-", suffix=".stage", dir=output.parent
        )
    )
    commands: list[tuple[str, ...]] = []
    progress_sink(
        emit_progress(
            event="item_started",
            task=task,
            item=item,
            index=1,
            total=1,
            stage="multi_look",
            status="start",
        )
    )
    try:
        multi_look = (
            "multi_look",
            str(plan.reference_asset.data_path),
            str(plan.reference_asset.parameter_path),
            str(_staged(plan.reference_mli, output, staging)),
            str(_staged(plan.reference_mli_par, output, staging)),
            str(plan.case.range_looks),
            str(plan.case.azimuth_looks),
        )
        commands.append(
            _run_command(
                "01_multi_look",
                multi_look,
                staging=staging,
                environment=run_environment,
            )
        )
        progress_sink(
            emit_progress(
                event="item_progress",
                task=task,
                item=item,
                index=1,
                total=1,
                stage="geocoding",
                status="running",
            )
        )
        commands.append(
            _run_command(
                "02_geocoding",
                _geocoding_command(plan, staging),
                staging=staging,
                environment=run_environment,
            )
        )

        staged_height = _staged(plan.wgs84_radar_height, output, staging)
        if not staged_height.is_file() or staged_height.stat().st_size < 1:
            raise ValueError(
                f"required WGS84 radar height was not generated: {staged_height.name}"
            )
        for destination in plan.expected_outputs:
            if destination == plan.manifest:
                continue
            candidate = _staged(destination, output, staging)
            if not candidate.is_file() or candidate.stat().st_size < 1:
                raise ValueError(f"required reference-geometry output is missing: {candidate.name}")

        manifest = {
            "schema_version": 1,
            "backend": "gamma",
            "geometry_type": "RADAR_GRID",
            "reference_date": plan.case.reference_date,
            "primary_polarization": plan.case.primary_polarization,
            "looks": {
                "range": plan.case.range_looks,
                "azimuth": plan.case.azimuth_looks,
            },
            "gamma_version": run_environment.get("GAMMA_VERSION", "unknown"),
            "input_signature": signature,
            "inputs": input_records,
            "commands": [_recorded_command(command, staging) for command in commands],
            "outputs": [
                str(path.relative_to(output)) for path in plan.expected_outputs
            ],
        }
        staged_manifest = _staged(plan.manifest, output, staging)
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if output.exists():
            raise FileExistsError(f"reference-geometry destination appeared during run: {output}")
        os.replace(staging, output)
        progress_sink(
            emit_progress(
                event="item_finished",
                task=task,
                item=item,
                index=1,
                total=1,
                stage="publish",
                status="complete",
            )
        )
        return GammaRadarGeoRefResult("complete", output, plan.manifest)
    except BaseException as error:
        failure_logs = _preserve_failure_logs(staging, output)
        progress_sink(
            emit_progress(
                event="item_finished",
                task=task,
                item=item,
                index=1,
                total=1,
                stage="failed",
                status="failed",
                failure_logs=None if failure_logs is None else str(failure_logs),
            )
        )
        if isinstance(error, GammaRadarGeoRefCommandError) and failure_logs is not None:
            preserved_stderr = failure_logs / error.stderr_log.relative_to(staging)
            raise GammaRadarGeoRefCommandError(
                error.stage, error.exit_code, error.command, preserved_stderr
            ) from None
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


__all__ = [
    "GammaRadarGeoRefCommandError",
    "GammaRadarGeoRefResult",
    "run_gamma_radar_geo_ref",
]
