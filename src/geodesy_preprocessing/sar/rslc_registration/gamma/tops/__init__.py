"""Public GAMMA TOPS geometry and coregistration interfaces.

功能概述:
    导出 Sentinel-1 TOPS 参考几何和配准的配置、计划及运行接口。

函数说明:
    ``GammaTopsGeoRefCase``、``GammaTopsGeoRefPlan``、
    ``GammaTopsCoregCase`` 和 ``GammaTopsCoregPlan`` 描述两个连续阶段。
"""

from .coreg_config import GammaTopsCoregCase
from .coreg_plan import GammaTopsCoregPlan, build_gamma_tops_coreg_plan
from .coreg_runner import GammaTopsCoregResult, run_gamma_tops_coreg
from .geo_ref_config import GammaTopsGeoRefCase
from .geo_ref_plan import GammaTopsGeoRefPlan, build_gamma_tops_geo_ref_plan
from .geo_ref_runner import GammaTopsGeoRefResult, run_gamma_tops_geo_ref

__all__ = [
    "GammaTopsCoregCase",
    "GammaTopsCoregPlan",
    "GammaTopsCoregResult",
    "GammaTopsGeoRefCase",
    "GammaTopsGeoRefPlan",
    "GammaTopsGeoRefResult",
    "build_gamma_tops_coreg_plan",
    "build_gamma_tops_geo_ref_plan",
    "run_gamma_tops_coreg",
    "run_gamma_tops_geo_ref",
]
