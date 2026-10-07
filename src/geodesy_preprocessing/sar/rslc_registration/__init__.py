"""Backend-separated SAR registration interfaces.

功能概述:
    提供配准阶段的公共结果边界和各处理后端的独立实现空间。

函数说明:
    ``RegistrationResult`` 是供 Workflow 和后续组件读取的后端中立结果。
"""

from .common import RegistrationResult

__all__ = ["RegistrationResult"]
