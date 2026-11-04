"""Completion-aware reuse decisions for expensive preprocessing products.

功能概述:
    只把清单完整、输入签名一致且科学产品通过校验的目录判为可复用；存在但
    残缺、来源变化或为符号链接的目录保持只读并给出删除或换目录提示。

函数说明:
    ``ReuseState`` 表示缺失、完整、残缺和签名不匹配；``classify_output``
    读取完成清单并调用工作流校验器；``ReuseDecision`` 提供稳定诊断文本。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class ReuseState(str, Enum):
    """Filesystem states relevant to safe expensive-product reuse."""

    ABSENT = "absent"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class ReuseDecision:
    """One non-mutating decision about an output directory."""

    state: ReuseState
    output_dir: Path
    reason: str

    @property
    def reusable(self) -> bool:
        return self.state is ReuseState.COMPLETE

    @property
    def guidance(self) -> str:
        if self.state in {ReuseState.ABSENT, ReuseState.COMPLETE}:
            return self.reason
        return (
            f"{self.reason}. Delete {self.output_dir} after inspection, or choose "
            "a different output directory; existing scientific products were not modified"
        )


Validator = Callable[[Path, dict[str, Any]], bool]


def _decision(state: ReuseState, output: Path, reason: str) -> ReuseDecision:
    return ReuseDecision(state=state, output_dir=output, reason=reason)


def classify_output(
    output_dir: str | Path,
    manifest_name: str,
    expected_signature: str,
    validator: Validator,
) -> ReuseDecision:
    """Classify ``output_dir`` without creating, deleting, or replacing anything."""

    output = Path(output_dir).expanduser()
    manifest_component = Path(manifest_name)
    if (
        manifest_component.is_absolute()
        or len(manifest_component.parts) != 1
        or manifest_name in {"", ".", ".."}
    ):
        raise ValueError("manifest_name must be one safe filename")
    if not isinstance(expected_signature, str) or not expected_signature:
        raise ValueError("expected_signature must be a non-empty string")
    if output.is_symlink():
        return _decision(
            ReuseState.MISMATCH,
            output,
            "output directory is a symbolic link and cannot be safely reused",
        )
    if not output.exists():
        return _decision(ReuseState.ABSENT, output, "output directory does not exist")
    if not output.is_dir():
        return _decision(
            ReuseState.INCOMPLETE, output, "output path exists but is not a directory"
        )

    manifest_path = output / manifest_name
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return _decision(
            ReuseState.INCOMPLETE,
            output,
            f"incomplete output: completion manifest is missing: {manifest_path}",
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return _decision(
            ReuseState.INCOMPLETE,
            output,
            f"completion manifest is unreadable or malformed: {error}",
        )
    if not isinstance(payload, Mapping):
        return _decision(
            ReuseState.INCOMPLETE,
            output,
            "completion manifest root is not a JSON object",
        )
    manifest = dict(payload)
    if manifest.get("status") != "complete":
        return _decision(
            ReuseState.INCOMPLETE,
            output,
            "completion manifest does not declare status=complete",
        )
    recorded_signature = manifest.get("input_signature")
    if recorded_signature != expected_signature:
        return _decision(
            ReuseState.MISMATCH,
            output,
            "completion manifest input signature does not match current inputs",
        )
    try:
        valid = validator(output, manifest)
    except (OSError, TypeError, ValueError, KeyError) as error:
        return _decision(
            ReuseState.INCOMPLETE,
            output,
            f"scientific product validation failed: {error}",
        )
    if not valid:
        return _decision(
            ReuseState.INCOMPLETE,
            output,
            "one or more required scientific products are missing or empty",
        )
    return _decision(
        ReuseState.COMPLETE,
        output,
        "completion manifest, input signature, and scientific products are valid",
    )


__all__ = ["ReuseDecision", "ReuseState", "classify_output"]
