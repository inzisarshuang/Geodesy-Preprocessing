"""Backend-neutral Sentinel-1 acquisition helpers.

功能概述:
    提供 Sentinel-1 获取配置、产品模型、查询、下载、BURST 合并、几何、参考表和
    覆盖图功能。

函数说明:
    具体接口位于 ``config``、``models``、``query``、``download``、``merge``、
    ``burst_table``、``burst_table_cli`` 和 ``coverage``。
"""

from .config import (
    load_sentinel1_acquisition_config,
    select_sentinel1_acquisition_case,
)
from .models import Sentinel1AcquisitionCase, Sentinel1Product, Sentinel1ProductType

__all__ = [
    "Sentinel1AcquisitionCase",
    "Sentinel1Product",
    "Sentinel1ProductType",
    "load_sentinel1_acquisition_config",
    "select_sentinel1_acquisition_case",
]
