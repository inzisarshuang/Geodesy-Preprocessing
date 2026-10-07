"""Common SAR inventory formats.

功能概述:
    提供不依赖卫星或处理后端的日期清单格式。

函数说明:
    本模块重导出 ``write_images_inventory``。
"""

from .images import write_images_inventory

__all__ = ["write_images_inventory"]
