"""Vendor adapters for supported GAMMA radar-grid products.

功能概述:
    汇集各卫星供应商产品的发现、暂存和 GAMMA SLC 计划适配器。

函数说明:
    本模块不定义函数；``ALOS2Adapter`` 是当前公开的 ALOS/PALSAR 适配器。
"""

from .alos2 import ALOS2Adapter
from .cosmo import CsgAdapter, CsksAdapter
from .lt1 import Lt1Adapter
from .nisar import NisarRslcAdapter
from .rsat2 import Rsat2Adapter
from .saocom import SaocomAdapter, saocom_polarizations_for_mode

__all__ = [
    "ALOS2Adapter",
    "CsgAdapter",
    "CsksAdapter",
    "Lt1Adapter",
    "NisarRslcAdapter",
    "Rsat2Adapter",
    "SaocomAdapter",
    "saocom_polarizations_for_mode",
]
