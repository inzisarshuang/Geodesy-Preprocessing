"""Immutable backend-neutral descriptions of registered SAR stacks.

功能概述:
    描述后端身份、配准数据网格类型和可供分析阶段读取的完整配准栈。

函数说明:
    ``GeometryType`` 区分雷达网格与地理编码网格；``BackendIdentity``
    记录处理后端及版本；``RegisteredStackAsset`` 绑定日期、极化和文件；
    ``RegisteredStackManifest`` 记录完整配准栈元数据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal


_BACKEND_NAME = re.compile(r"[a-z][a-z0-9_.-]*\Z")
_BACKEND_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")


class GeometryType(str, Enum):
    """Geometry family used by every acquisition in a registered stack."""

    RADAR_GRID = "radar_grid"
    GEOCODED_GRID = "geocoded_grid"


@dataclass(frozen=True)
class BackendIdentity:
    """Stable processing-backend name and its reported version."""

    name: str
    version: str

    def __post_init__(self) -> None:
        if not _BACKEND_NAME.fullmatch(self.name):
            raise ValueError(f"unsafe backend name: {self.name!r}")
        if not _BACKEND_VERSION.fullmatch(self.version):
            raise ValueError(f"unsafe backend version: {self.version!r}")


@dataclass(frozen=True)
class RegisteredStackAsset:
    """One registered complex image and its parameter file."""

    date: str
    polarization: str
    data_path: Path
    parameter_path: Path

    def __post_init__(self) -> None:
        try:
            parsed = datetime.strptime(self.date, "%Y%m%d")
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid asset date: {self.date!r}") from error
        if parsed.strftime("%Y%m%d") != self.date:
            raise ValueError(f"invalid asset date: {self.date!r}")
        if (
            not isinstance(self.polarization, str)
            or not self.polarization
            or self.polarization != self.polarization.upper()
        ):
            raise ValueError("asset polarization must be a non-empty uppercase label")
        for field in ("data_path", "parameter_path"):
            if not isinstance(getattr(self, field), Path):
                raise TypeError(f"asset {field} must be a Path")
        if self.data_path == self.parameter_path:
            raise ValueError("asset data and parameter paths must differ")


@dataclass(frozen=True)
class RegisteredStackManifest:
    """Backend-neutral metadata for one complete registered complex stack."""

    schema_version: int
    status: Literal["complete"]
    sensor: str
    source_product_type: str
    backend: BackendIdentity
    geometry_type: GeometryType
    reference_date: str
    dates: tuple[str, ...]
    frequencies: tuple[str, ...]
    polarizations: tuple[str, ...]
    assets: tuple[RegisteredStackAsset, ...]
    orbit_direction: str | None = None
    track: str | None = None
    frame: str | None = None
    coordinate_system: str | None = None
    pixel_spacing: tuple[float, float] | None = None
    origin: tuple[float, float] | None = None
    shape: tuple[int, int] | None = None
    common_extent: tuple[float, float, float, float] | None = None
    quality_path: Path | None = None
    provenance_path: Path | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("registered stack schema_version must be 2")
        if self.status != "complete":
            raise ValueError("registered stack status must be 'complete'")
        if not isinstance(self.sensor, str) or not self.sensor.strip():
            raise ValueError("sensor must not be empty")
        if not isinstance(self.source_product_type, str) or not self.source_product_type.strip():
            raise ValueError("source_product_type must not be empty")
        if not isinstance(self.backend, BackendIdentity):
            raise TypeError("backend must be BackendIdentity")
        if not isinstance(self.geometry_type, GeometryType):
            raise TypeError("geometry_type must be GeometryType")
        for field in ("dates", "frequencies", "polarizations", "assets"):
            if not isinstance(getattr(self, field), tuple):
                raise TypeError(f"{field} must be an immutable tuple")
        self._validate_dates()
        self._validate_labels("frequencies", self.frequencies)
        self._validate_labels("polarizations", self.polarizations)
        self._validate_assets()
        for field, length in (
            ("pixel_spacing", 2),
            ("origin", 2),
            ("shape", 2),
            ("common_extent", 4),
        ):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, tuple) or len(value) != length):
                raise TypeError(f"{field} must be an immutable {length}-item tuple")
        for field in ("quality_path", "provenance_path"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, Path):
                raise TypeError(f"{field} must be a Path")

    def _validate_dates(self) -> None:
        if not self.dates:
            raise ValueError("dates must not be empty")
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("dates contain a duplicate date")
        for value in self.dates:
            try:
                parsed = datetime.strptime(value, "%Y%m%d")
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid acquisition date: {value!r}") from error
            if parsed.strftime("%Y%m%d") != value:
                raise ValueError(f"invalid acquisition date: {value!r}")
        if self.reference_date not in self.dates:
            raise ValueError("reference date must be present in dates")

    def _validate_assets(self) -> None:
        if not self.assets or not all(
            isinstance(asset, RegisteredStackAsset) for asset in self.assets
        ):
            raise ValueError("assets must contain at least one RegisteredStackAsset")
        identities = tuple((asset.date, asset.polarization) for asset in self.assets)
        if len(set(identities)) != len(identities):
            raise ValueError("assets contain a duplicate date/polarization identity")
        if {asset.date for asset in self.assets} != set(self.dates):
            raise ValueError("asset dates must match manifest dates")
        if {asset.polarization for asset in self.assets} != set(self.polarizations):
            raise ValueError("asset polarizations must match manifest polarizations")
        expected = {
            (date, polarization)
            for date in self.dates
            for polarization in self.polarizations
        }
        if set(identities) != expected:
            raise ValueError("assets must form a complete date/polarization grid")

    @staticmethod
    def _validate_labels(field: str, values: tuple[str, ...]) -> None:
        if not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"{field} must contain non-empty strings")
        if len(set(values)) != len(values):
            raise ValueError(f"{field} must not contain duplicates")


__all__ = [
    "BackendIdentity",
    "GeometryType",
    "RegisteredStackAsset",
    "RegisteredStackManifest",
]
