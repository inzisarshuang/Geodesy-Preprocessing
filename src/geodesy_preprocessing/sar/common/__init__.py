"""Backend-neutral SAR contracts.

功能概述:
    导出可由 GAMMA、ISCE3 和后续独立算法共同使用的 SAR 数据契约。

函数说明:
    本模块重导出 ``BackendIdentity``、``GeometryType``、
    ``RegisteredStackAsset``、``RegisteredStackManifest``、``read_stack_manifest`` 和
    ``write_stack_manifest``。
"""

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

__all__ = [
    "BackendIdentity",
    "GeometryType",
    "RegisteredStackAsset",
    "RegisteredStackManifest",
    "read_stack_manifest",
    "write_stack_manifest",
]
