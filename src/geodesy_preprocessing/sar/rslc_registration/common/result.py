"""Backend-neutral result returned after native registration validation.

功能概述:
    用稳定字段把已经验证的配准栈交给 Workflow、Analysis 或 Adapter，而不暴露
    GAMMA 参数对象或某一种后端的执行计划。

函数说明:
    ``RegistrationResult`` 保存清单、参考日期、日期序列、质量记录和原生根目录。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RegistrationResult:
    """Validated backend-neutral location summary for one registered stack."""

    manifest_path: Path
    reference_date: str
    dates: tuple[str, ...]
    quality_path: Path | None
    native_root: Path

    def __post_init__(self) -> None:
        for field in ("manifest_path", "native_root"):
            if not isinstance(getattr(self, field), Path):
                raise TypeError(f"{field} must be a Path")
        if self.quality_path is not None and not isinstance(self.quality_path, Path):
            raise TypeError("quality_path must be a Path or None")
        if not isinstance(self.dates, tuple) or not self.dates:
            raise TypeError("dates must be a non-empty immutable tuple")
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("dates contain a duplicate date")
        for date in self.dates:
            try:
                parsed = datetime.strptime(date, "%Y%m%d")
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid acquisition date: {date!r}") from error
            if parsed.strftime("%Y%m%d") != date:
                raise ValueError(f"invalid acquisition date: {date!r}")
        if self.reference_date not in self.dates:
            raise ValueError("reference date must be present in dates")


__all__ = ["RegistrationResult"]
