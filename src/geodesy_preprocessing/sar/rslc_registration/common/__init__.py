"""Backend-neutral SAR registration results.

功能概述:
    导出不包含 GAMMA、ISCE 或其他软件专用参数的配准结果。

函数说明:
    ``RegistrationResult`` 描述已验证配准栈的清单和原生产品位置。
"""

from .result import RegistrationResult

__all__ = ["RegistrationResult"]
