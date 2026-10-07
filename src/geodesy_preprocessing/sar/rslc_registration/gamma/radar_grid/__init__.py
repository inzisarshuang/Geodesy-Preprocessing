"""GAMMA 普通雷达坐标 SLC 配准的公共入口。

功能概述:
    汇总普通雷达坐标 SLC 配准所需的数据模型和参数读取函数。

函数说明:
    本模块仅重新导出子模块已经说明的公共接口，不定义新函数。
"""

from .models import GammaSlcAsset, GammaSlcGrid
from .parameters import read_gamma_value, read_slc_grid
from .geo_ref_plan import build_gamma_radar_geo_ref_plan
from .geo_ref_runner import run_gamma_radar_geo_ref
from .coreg_plan import build_gamma_radar_coreg_plan
from .coreg_runner import run_gamma_radar_coreg

__all__ = [
    "GammaSlcAsset",
    "GammaSlcGrid",
    "build_gamma_radar_coreg_plan",
    "build_gamma_radar_geo_ref_plan",
    "read_gamma_value",
    "read_slc_grid",
    "run_gamma_radar_coreg",
    "run_gamma_radar_geo_ref",
]
