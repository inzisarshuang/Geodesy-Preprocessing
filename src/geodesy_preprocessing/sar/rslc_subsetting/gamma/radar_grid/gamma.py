"""External GAMMA command execution for registered-stack subsetting.

功能概述:
    以参数数组执行 GAMMA 命令，并保留可供日志和错误报告使用的结果。

函数说明:
    ``GammaCommandResult`` 记录一次命令；``GammaCommandError`` 表示非零退出；
    ``GammaCommandRunner`` 执行真实外部命令。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class GammaCommandResult:
    """Captured result of one GAMMA command."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class GammaCommandError(RuntimeError):
    """A GAMMA command that returned a non-zero exit status."""

    def __init__(self, result: GammaCommandResult) -> None:
        self.result = result
        super().__init__(
            f"GAMMA command {result.command[0]!r} exited with {result.returncode}"
        )


class GammaCommandRunner:
    """Run GAMMA commands without a shell and capture their complete output."""

    def run(self, command: Sequence[str]) -> GammaCommandResult:
        """Execute one argument-safe external command."""

        normalized = tuple(str(value) for value in command)
        completed = subprocess.run(
            normalized,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = GammaCommandResult(
            normalized,
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )
        if result.returncode:
            raise GammaCommandError(result)
        return result


__all__ = ["GammaCommandError", "GammaCommandResult", "GammaCommandRunner"]
