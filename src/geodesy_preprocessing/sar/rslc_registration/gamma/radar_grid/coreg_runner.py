"""GAMMA 普通雷达坐标 SLC 标准配准与事务式栈发布。

功能概述:
    为输入创建只读暂存视图，复制参考景、逐景调用 ``SLC_coreg.py``，严格解析质量，
    最后一次性发布 RSLC、质量、溯源、日期表及后端中立栈清单。

函数说明:
    ``GammaRadarCoregCommandError`` 描述命令失败；
    ``run_gamma_radar_coreg`` 返回后端中立 ``RegistrationResult``。
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from geodesy_preprocessing.common.progress import emit_progress
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
from geodesy_preprocessing.sar.rslc_registration.common import RegistrationResult

from .coreg_plan import (
    GammaRadarCommandPlan,
    GammaRadarCoregPlan,
    GammaRadarScenePlan,
)
from .publication import classify_stack_destination
from .quality import classify_coreg_quality, parse_coreg_quality


class GammaRadarCoregCommandError(RuntimeError):
    """Failure of one external GAMMA registration command."""

    def __init__(
        self, stage: str, exit_code: int, command: Sequence[str], stderr_log: Path
    ) -> None:
        self.stage = stage
        self.exit_code = exit_code
        self.command = tuple(command)
        self.stderr_log = stderr_log
        super().__init__(f"{stage} exited with code {exit_code}; see {stderr_log}")


def _registration_result(root: Path) -> RegistrationResult:
    manifest_path = root / "registered_stack.json"
    manifest = read_stack_manifest(manifest_path)
    return RegistrationResult(
        manifest_path=manifest_path,
        reference_date=manifest.reference_date,
        dates=manifest.dates,
        quality_path=root / manifest.quality_path if manifest.quality_path else None,
        native_root=root,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _input_signature(plan: GammaRadarCoregPlan) -> str:
    case = plan.case
    assets = []
    for scene in plan.scenes:
        stat = scene.asset.data_path.stat()
        assets.append(
            {
                "date": scene.asset.date,
                "polarization": scene.asset.polarization,
                "data": str(scene.asset.data_path),
                "data_size": stat.st_size,
                "data_mtime_ns": stat.st_mtime_ns,
                "parameter": str(scene.asset.parameter_path),
                "parameter_sha256": _sha256(scene.asset.parameter_path),
            }
        )
    payload = {
        "assets": assets,
        "images_sha256": _sha256(case.images_file),
        "reference_geometry_sha256": _sha256(case.reference_geometry),
        "height_sha256": _sha256(plan.height_map),
        "sensor": case.sensor,
        "frequency": case.frequency,
        "track": case.track,
        "frame": case.frame,
        "orbit_direction": case.orbit_direction,
        "reference_date": case.reference_date,
        "primary_polarization": case.primary_polarization,
        "selected_polarizations": list(case.selected_polarizations),
        "range_looks": case.range_looks,
        "azimuth_looks": case.azimuth_looks,
        "refinement": case.refinement,
        "sar_mode": case.sar_mode,
        "offset_parameters": {
            "offset_polynomial_parameters": case.offset_polynomial_parameters,
            "offset_range_estimates": case.offset_range_estimates,
            "offset_azimuth_estimates": case.offset_azimuth_estimates,
            "offset_range_window": case.offset_range_window,
            "offset_azimuth_window": case.offset_azimuth_window,
            "offset_oversampling": case.offset_oversampling,
            "offset_correlation_threshold": case.offset_correlation_threshold,
            "refine_range_step": case.refine_range_step,
            "refine_azimuth_step": case.refine_azimuth_step,
            "refine_range_window": case.refine_range_window,
            "refine_azimuth_window": case.refine_azimuth_window,
            "refine_oversampling": case.refine_oversampling,
            "refine_correlation_threshold": case.refine_correlation_threshold,
        },
        "quality_thresholds": {
            "warning_offset_stddev": case.quality_thresholds.warning_offset_stddev,
            "failure_offset_stddev": case.quality_thresholds.failure_offset_stddev,
            "warning_residual_offset": case.quality_thresholds.warning_residual_offset,
            "failure_residual_offset": case.quality_thresholds.failure_residual_offset,
        },
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


def _stage_path(path: Path, output: Path, staging: Path) -> Path:
    return staging / path.relative_to(output)


def _input_view(scene: GammaRadarScenePlan, staging: Path) -> tuple[Path, Path]:
    directory = staging / "input_view" / scene.asset.polarization / scene.asset.date
    directory.mkdir(parents=True, exist_ok=True)
    data = directory / scene.asset.data_path.name
    parameter = directory / scene.asset.parameter_path.name
    data.symlink_to(scene.asset.data_path)
    parameter.symlink_to(scene.asset.parameter_path)
    return data, parameter


def _run(
    stage: str,
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log_dir: Path,
) -> tuple[str, ...]:
    executable = shutil.which(command[0], path=environment.get("PATH"))
    if executable is None:
        raise FileNotFoundError(f"required command not found in PATH: {command[0]}")
    resolved = (executable, *(str(value) for value in command[1:]))
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{stage}.stdout.log"
    stderr_path = log_dir / f"{stage}.stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            resolved,
            cwd=cwd,
            env=dict(environment),
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
            shell=False,
        )
    if completed.returncode != 0:
        raise GammaRadarCoregCommandError(
            stage, completed.returncode, resolved, stderr_path
        )
    return tuple(resolved)


def _command_for_stage(
    scene: GammaRadarScenePlan,
    planned_command: GammaRadarCommandPlan,
    plan: GammaRadarCoregPlan,
    staging: Path,
    input_paths: dict[tuple[str, str], tuple[Path, Path]],
) -> tuple[str, ...]:
    command = list(planned_command.argv)
    asset_view = input_paths[(scene.asset.date, scene.asset.polarization)]
    reference_view = input_paths[
        (scene.reference_asset.date, scene.reference_asset.polarization)
    ]
    replacements = {
        str(scene.asset.data_path): str(asset_view[0]),
        str(scene.asset.parameter_path): str(asset_view[1]),
        str(scene.reference_asset.data_path): str(reference_view[0]),
        str(scene.reference_asset.parameter_path): str(reference_view[1]),
    }
    return tuple(
        replacements.get(
            value,
            value.replace("{staging}", str(staging)) if "{staging}" in value else value,
        )
        for value in command
    )


def _nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size < 1:
        raise ValueError(f"required {label} was not generated: {path}")


def _publish_metadata(
    plan: GammaRadarCoregPlan,
    staging: Path,
    quality_records: dict[str, dict[str, object]],
    commands: list[tuple[str, ...]],
    gamma_version: str,
    input_signature: str,
) -> None:
    staging_text = str(staging)

    def stable_command(command: tuple[str, ...]) -> list[str]:
        return [
            "$STACK" + value[len(staging_text) :]
            if value.startswith(staging_text)
            else value
            for value in command
        ]

    quality_path = staging / "quality.json"
    quality_path.write_text(
        json.dumps({"scenes": quality_records}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    provenance_path = staging / "provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "backend": "gamma",
                "gamma_version": gamma_version,
                "sensor": plan.case.sensor,
                "frequency_label": plan.case.frequency,
                "center_frequency_hz": plan.center_frequency_hz,
                "input_signature": input_signature,
                "commands": [stable_command(command) for command in commands],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (staging / "images").write_text(
        "".join(f"{date}\n" for date in plan.dates), encoding="ascii"
    )
    assets = tuple(
        RegisteredStackAsset(
            date=scene.asset.date,
            polarization=scene.asset.polarization,
            data_path=_stage_path(scene.outputs.rslc, plan.case.output_dir, staging).relative_to(staging),
            parameter_path=_stage_path(scene.outputs.rslc_par, plan.case.output_dir, staging).relative_to(staging),
        )
        for scene in plan.scenes
    )
    reference_grid = next(
        scene.grid
        for scene in plan.scenes
        if scene.asset.date == plan.case.reference_date
        and scene.asset.polarization == plan.case.primary_polarization
    )
    manifest = RegisteredStackManifest(
        schema_version=2,
        status="complete",
        sensor=plan.case.sensor,
        source_product_type="SLC",
        backend=BackendIdentity("gamma", gamma_version),
        geometry_type=GeometryType.RADAR_GRID,
        reference_date=plan.case.reference_date,
        dates=plan.dates,
        frequencies=(format(plan.center_frequency_hz, ".17g"),),
        polarizations=plan.case.selected_polarizations,
        assets=assets,
        orbit_direction=plan.case.orbit_direction,
        track=plan.case.track,
        frame=plan.case.frame,
        pixel_spacing=(reference_grid.range_spacing, reference_grid.azimuth_spacing),
        shape=(reference_grid.azimuth_lines, reference_grid.range_samples),
        quality_path=Path("quality.json"),
        provenance_path=Path("provenance.json"),
    )
    write_stack_manifest(manifest, staging / "registered_stack.json")


def run_gamma_radar_coreg(
    plan: GammaRadarCoregPlan,
    *,
    environment: Mapping[str, str] | None = None,
    progress_sink: Callable[[str], None] = print,
) -> RegistrationResult:
    """Run standard registration and atomically publish a quality-approved stack."""

    state = classify_stack_destination(plan.case.output_dir)
    signature = _input_signature(plan)
    if state == "complete":
        provenance_path = plan.case.output_dir / "provenance.json"
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FileExistsError(
                f"complete RSLC destination has invalid provenance: {plan.case.output_dir}"
            ) from error
        if provenance.get("input_signature") != signature:
            raise FileExistsError("RSLC destination was produced from different inputs")
        return _registration_result(plan.case.output_dir)
    run_environment = os.environ.copy()
    if environment:
        run_environment.update((str(key), str(value)) for key, value in environment.items())
    gamma_version = run_environment.get("GAMMA_VERSION", "unknown")
    output = plan.case.output_dir
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}-", suffix=".stage", dir=output.parent)
    )
    commands: list[tuple[str, ...]] = []
    quality_records: dict[str, dict[str, object]] = {}
    input_paths: dict[tuple[str, str], tuple[Path, Path]] = {}
    current_identity: str | None = None
    current_index: int | None = None
    for scene in plan.scenes:
        input_paths[(scene.asset.date, scene.asset.polarization)] = _input_view(
            scene, staging
        )
    try:
        for index, scene in enumerate(plan.scenes, start=1):
            identity = f"{scene.asset.date}/{scene.asset.polarization}"
            current_identity = identity
            current_index = index
            progress_sink(
                emit_progress(
                    event="item_started",
                    task="PRE_GAMMA_SAR_RADAR_COREG",
                    item=identity,
                    index=index,
                    total=len(plan.scenes),
                    stage="reference_copy" if scene.copy_reference else "coregister",
                    status="start",
                )
            )
            outputs = scene.outputs
            staged_rslc = _stage_path(outputs.rslc, output, staging)
            staged_par = _stage_path(outputs.rslc_par, output, staging)
            staged_rmli = _stage_path(outputs.rmli, output, staging)
            staged_rmli_par = _stage_path(outputs.rmli_par, output, staging)
            staged_rslc.parent.mkdir(parents=True, exist_ok=True)
            log_dir = staging / "logs" / scene.asset.date / scene.asset.polarization
            if scene.copy_reference:
                shutil.copyfile(scene.asset.data_path, staged_rslc)
                shutil.copyfile(scene.asset.parameter_path, staged_par)
                command = (
                    "multi_look",
                    str(staged_rslc),
                    str(staged_par),
                    str(staged_rmli),
                    str(staged_rmli_par),
                    str(plan.case.range_looks),
                    str(plan.case.azimuth_looks),
                )
                commands.append(
                    _run(
                        "reference_multi_look",
                        command,
                        cwd=staging,
                        environment=run_environment,
                        log_dir=log_dir,
                    )
                )
                quality_records[identity] = {"status": "reference"}
            elif scene.asset.polarization == plan.case.primary_polarization:
                command = _command_for_stage(
                    scene, scene.commands[0], plan, staging, input_paths
                )
                commands.append(
                    _run(
                        "standard_coreg",
                        command,
                        cwd=staging,
                        environment=run_environment,
                        log_dir=log_dir,
                    )
                )
                input_data = input_paths[(scene.asset.date, scene.asset.polarization)][0]
                generated_geometry = {
                    Path(str(input_data) + ".lt_fine"): _stage_path(outputs.lookup_table, output, staging),
                    Path(str(input_data) + ".off"): _stage_path(outputs.offset_parameter, output, staging),
                }
                for source, destination in generated_geometry.items():
                    _nonempty(source, destination.name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source.replace(destination)
                standard_quality = Path(str(staged_rslc) + ".coreg_quality")
                _nonempty(standard_quality, "standard coregistration quality")
                final_quality = _stage_path(outputs.coreg_quality, output, staging)
                final_quality.parent.mkdir(parents=True, exist_ok=True)
                if len(scene.commands) == 1:
                    standard_quality.replace(final_quality)
                else:
                    parse_coreg_quality(standard_quality)
                    standard_quality.replace(log_dir / "standard.coreg_quality")
                    refine = _command_for_stage(
                        scene, scene.commands[1], plan, staging, input_paths
                    )
                    commands.append(
                        _run(
                            "offset_field_refine",
                            refine,
                            cwd=staging,
                            environment=run_environment,
                            log_dir=log_dir,
                        )
                    )
                    _nonempty(final_quality, "refined coregistration quality")
                parsed = parse_coreg_quality(final_quality)
                status = classify_coreg_quality(parsed, plan.case.quality_thresholds)
                quality_records[identity] = {
                    "status": status,
                    "accepted_offsets": parsed.accepted_offsets,
                    "total_offsets": parsed.total_offsets,
                    "range_stddev": parsed.range_stddev,
                    "azimuth_stddev": parsed.azimuth_stddev,
                    "range_residual": parsed.range_residual,
                    "azimuth_residual": parsed.azimuth_residual,
                }
                if status == "failed":
                    raise ValueError(f"hard quality threshold failed for {identity}")
            else:
                for planned_command in scene.commands:
                    command = _command_for_stage(
                        scene, planned_command, plan, staging, input_paths
                    )
                    commands.append(
                        _run(
                            planned_command.stage,
                            command,
                            cwd=staging,
                            environment=run_environment,
                            log_dir=log_dir,
                        )
                    )
                primary_identity = (
                    f"{scene.asset.date}/{plan.case.primary_polarization}"
                )
                primary_quality = quality_records[primary_identity]
                quality_records[identity] = {
                    "status": primary_quality["status"],
                    "geometry_reused_from": primary_identity,
                }
            for path, label in (
                (staged_rslc, "RSLC"),
                (staged_par, "RSLC parameter"),
                (staged_rmli, "RMLI"),
                (staged_rmli_par, "RMLI parameter"),
            ):
                _nonempty(path, label)
            progress_sink(
                emit_progress(
                    event="item_finished",
                    task="PRE_GAMMA_SAR_RADAR_COREG",
                    item=identity,
                    index=index,
                    total=len(plan.scenes),
                    stage="quality",
                    status=quality_records[identity]["status"],
                )
            )
            current_identity = None
            current_index = None
        shutil.rmtree(staging / "input_view")
        _publish_metadata(
            plan, staging, quality_records, commands, gamma_version, signature
        )
        if output.exists():
            raise FileExistsError(f"RSLC destination appeared during execution: {output}")
        os.replace(staging, output)
        return _registration_result(output)
    except BaseException as error:
        failure_logs = _preserve_failure_logs(staging, output)
        progress_sink(
            emit_progress(
                event="item_finished",
                task="PRE_GAMMA_SAR_RADAR_COREG",
                item=current_identity,
                index=current_index,
                total=len(plan.scenes),
                stage="failed",
                status="failed",
                failure_logs=None if failure_logs is None else str(failure_logs),
            )
        )
        if isinstance(error, GammaRadarCoregCommandError) and failure_logs is not None:
            preserved_stderr = failure_logs / error.stderr_log.relative_to(staging)
            raise GammaRadarCoregCommandError(
                error.stage, error.exit_code, error.command, preserved_stderr
            ) from None
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


__all__ = ["GammaRadarCoregCommandError", "run_gamma_radar_coreg"]
