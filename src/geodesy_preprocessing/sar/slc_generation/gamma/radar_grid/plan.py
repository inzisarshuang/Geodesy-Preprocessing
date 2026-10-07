"""Batch planning for GAMMA radar-grid vendor-to-SLC conversion.

功能概述:
    选择卫星适配器、完成只读产品发现，并生成不执行外部命令的不可变批计划。

函数说明:
    ``GammaRadarSlcPlan`` 保存 case、适配器和产品清单；
    ``build_gamma_radar_slc_plan`` 构建计划；旧名称保持为同一对象的别名。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .adapters import (
    ALOS2Adapter,
    CsgAdapter,
    CsksAdapter,
    Lt1Adapter,
    NisarRslcAdapter,
    Rsat2Adapter,
    SaocomAdapter,
)
from .adapters.base import SensorAdapter
from .config import OtherSarSlcCase
from .models import SourceProduct

DEFAULT_ADAPTERS: Mapping[str, SensorAdapter] = MappingProxyType(
    {
        "alos2": ALOS2Adapter(),
        "csg": CsgAdapter(),
        "csks": CsksAdapter(),
        "lt1": Lt1Adapter(),
        "nisar": NisarRslcAdapter(),
        "rsat2": Rsat2Adapter(),
        "saocom": SaocomAdapter(),
    }
)


@dataclass(frozen=True)
class GammaRadarSlcPlan:
    """One validated case, its selected adapter, and ordered source products."""

    case: OtherSarSlcCase
    adapter: SensorAdapter
    products: tuple[SourceProduct, ...]


SlcBatchPlan = GammaRadarSlcPlan


def build_gamma_radar_slc_plan(
    case: OtherSarSlcCase,
    adapters: Mapping[str, SensorAdapter] = DEFAULT_ADAPTERS,
) -> GammaRadarSlcPlan:
    """Discover all products through the adapter selected by ``case.sensor``."""

    try:
        adapter = adapters[case.sensor]
    except KeyError as error:
        raise ValueError(f"no Other-SAR adapter registered for {case.sensor}") from error
    products = adapter.discover(case)
    if not products:
        raise ValueError(f"{case.raw_dir}: adapter returned no source products")
    return GammaRadarSlcPlan(case=case, adapter=adapter, products=products)


build_slc_plan = build_gamma_radar_slc_plan


__all__ = [
    "DEFAULT_ADAPTERS",
    "GammaRadarSlcPlan",
    "SlcBatchPlan",
    "build_gamma_radar_slc_plan",
    "build_slc_plan",
]
