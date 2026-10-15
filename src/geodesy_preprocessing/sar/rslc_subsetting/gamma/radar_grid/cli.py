"""Command-line interface for KML-driven GAMMA registered-stack cropping.

功能概述:
    从项目 CFG 生成可检查的裁剪计划，或执行完整 RSLC 批量裁剪和原子发布。

函数说明:
    ``main`` 解析 ``--config``、``--case``、``--plan-only`` 和 ``--force``。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from collections.abc import Sequence

from geodesy_preprocessing.common.case_layout import resolve_case_log_path

from .config import load_crop_config
from .gamma import GammaCommandError, GammaCommandRunner
from .masking import build_crop_geometry
from .plan import build_crop_plan
from .publication import atomic_publish_crop, crop_aoi_digest, crop_input_signature
from .runner import run_crop_stack


class _PlanLoggingRunner:
    def __init__(self, stdout_path: Path, stderr_path: Path) -> None:
        self.delegate = GammaCommandRunner()
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path

    def run(self, command):
        normalized = tuple(str(value) for value in command)
        with self.stdout_path.open("a", encoding="utf-8") as stdout:
            stdout.write("$ " + " ".join(normalized) + "\n")
        try:
            result = self.delegate.run(normalized)
        except GammaCommandError as error:
            with self.stdout_path.open("a", encoding="utf-8") as stdout:
                stdout.write(error.result.stdout)
            with self.stderr_path.open("a", encoding="utf-8") as stderr:
                stderr.write(error.result.stderr)
            raise
        with self.stdout_path.open("a", encoding="utf-8") as stdout:
            stdout.write(result.stdout)
        with self.stderr_path.open("a", encoding="utf-8") as stderr:
            stderr.write(result.stderr)
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodesy-pre-gamma-sar-rslc-crop",
        description="按 KML 缓冲范围检查并裁剪完整 GAMMA 注册 RSLC 栈。",
    )
    parser.add_argument("--config", required=True, type=Path, help="项目裁剪 CFG")
    parser.add_argument("--case", required=True, help="CFG 中的 case 名称")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="只生成覆盖率、掩膜和窗口检查目录，不运行 SLC_copy",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="原子替换冲突目标；不会修改完整源 RSLC",
    )
    return parser


def _existing_plan_matches(path: Path, signature: str) -> bool:
    try:
        payload = json.loads((path / "crop_window.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return payload.get("input_signature") == signature


def _publish_plan(plan, *, force: bool) -> tuple[Path, bool]:
    output = Path(f"{plan.output_dir}.plan")
    output.parent.mkdir(parents=True, exist_ok=True)
    signature = crop_input_signature(plan)
    if output.exists() and _existing_plan_matches(output, signature) and not force:
        return output, True
    if output.exists() and not force:
        raise FileExistsError(f"检查目录来自不同输入，请改名或使用 --force：{output}")
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.", suffix=".stage", dir=output.parent
        )
    )
    try:
        log_dir = (
            resolve_case_log_path(
                plan.config.case_root, "gamma_sar_rslc_crop_plan", plan.config.name
            )
            if plan.config.case_root is not None
            else plan.config.project_root
            / "LOG/PRE/GAMMA/gamma_sar_rslc_crop_plan"
            / plan.config.name
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / "stdout.log"
        stderr_log = log_dir / "stderr.log"
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
        geometry = build_crop_geometry(
            plan,
            staging / "geometry_work",
            runner=_PlanLoggingRunner(stdout_log, stderr_log),
        )
        for artifact in (
            geometry.buffered_geojson,
            geometry.map_mask_tif,
            geometry.radar_mask_bmp,
        ):
            artifact.replace(staging / artifact.name)
        shutil.rmtree(staging / "geometry_work")
        quicklooks = staging / "quicklooks"
        quicklooks.mkdir()
        shutil.copy2(staging / "aoi_mask_radar.bmp", quicklooks / "aoi_mask_radar.bmp")
        payload = {
            "aoi_sha256": crop_aoi_digest(plan),
            "buffer_km": plan.config.buffer_km,
            "coverage": {
                "buffered": geometry.coverage.buffered_ratio,
                "original": geometry.coverage.original_ratio,
            },
            "input_signature": signature,
            "polarizations": list(plan.selected_polarizations),
            "source_manifest": str(plan.config.registered_stack),
            "window": {
                "loff": geometry.window.loff,
                "nl": geometry.window.nl,
                "nr": geometry.window.nr,
                "roff": geometry.window.roff,
                "warnings": list(geometry.window.warnings),
            },
        }
        (staging / "crop_window.json").write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        atomic_publish_crop(staging, output, force=force)
        return output, False
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main(arguments: Sequence[str] | None = None) -> int:
    """Plan or execute one configured GAMMA registered-stack crop."""

    options = _parser().parse_args(arguments)
    try:
        config = load_crop_config(options.config, options.case)
        plan = build_crop_plan(
            config,
            allow_incomplete_output=options.force,
        )
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    try:
        if options.plan_only:
            output, skipped = _publish_plan(plan, force=options.force)
            if skipped:
                print(f"裁剪检查已经完成，输入没有变化：{output}")
            else:
                print(f"裁剪检查完成，请查看掩膜和窗口：{output}")
            return 0
        result = run_crop_stack(plan, force=options.force)
        if result.skipped:
            print(f"裁剪栈已经完成，输入没有变化：{result.output_dir}")
        else:
            print(f"裁剪栈完成：{result.completed}/{result.total}，结果：{result.output_dir}")
        return 0
    except KeyboardInterrupt:
        print("ERROR: 用户中断裁剪", file=sys.stderr)
        return 130
    except (GammaCommandError, FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
