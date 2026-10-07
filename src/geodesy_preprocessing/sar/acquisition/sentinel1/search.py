"""Sentinel-1 ASF 查询适配与产品元数据规范化。

功能概述:
    将 ASF 12.x 的 GeoJSON 产品记录转换为稳定、不含凭据的 Sentinel-1 产品模型。
    极化只在本地按包含关系判断，避免把 VV+VH 产品误排除。

函数说明:
    ``Sentinel1SearchClient`` 定义可注入查询边界；
    ``AsfSentinel1SearchClient`` 执行无认证元数据查询；
    ``search_sentinel1`` 校验、去重并确定性排序结果。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from shapely import wkt
from shapely.geometry import box, shape

from geodesy_preprocessing.sar.common.roi import load_kml_roi

from .models import Sentinel1AcquisitionCase, Sentinel1Product, Sentinel1ProductType


@runtime_checkable
class Sentinel1SearchClient(Protocol):
    """Provider boundary returning GeoJSON-like mappings only."""

    def search(self, case: Sentinel1AcquisitionCase) -> Sequence[Mapping[str, object]]:
        """Search one case without downloading product bytes."""


class AsfSentinel1SearchClient:
    """Lazy ASF adapter that does not send exact polarization filters."""

    def search(self, case: Sentinel1AcquisitionCase) -> tuple[Mapping[str, object], ...]:
        """Run one unauthenticated ASF metadata query."""

        import asf_search as asf

        aoi = load_kml_roi(case.kml_file) if case.kml_file else box(*case.bbox)
        level = {
            Sentinel1ProductType.BURST: asf.constants.PRODUCT_TYPE.BURST,
            Sentinel1ProductType.SLC: asf.constants.PRODUCT_TYPE.SLC,
            Sentinel1ProductType.GRD: asf.constants.PRODUCT_TYPE.GRD_HD,
        }[case.product_type]
        dataset = (
            asf.constants.DATASET.SLC_BURST
            if case.product_type is Sentinel1ProductType.BURST
            else asf.constants.DATASET.SENTINEL1
        )
        options: dict[str, object] = {
            "dataset": [dataset],
            "processingLevel": [level],
            "beamMode": case.beam_mode,
            "intersectsWith": wkt.dumps(aoi, rounding_precision=12, trim=True),
            "start": case.start_date,
            "end": case.end_date,
        }
        if case.tracks is not None:
            options["relativeOrbit"] = list(case.tracks)
        if case.orbit_direction is not None:
            options["flightDirection"] = case.orbit_direction
        return tuple(_as_mapping(item) for item in asf.geo_search(**options))


def _as_mapping(item: object) -> Mapping[str, object]:
    if isinstance(item, Mapping):
        return item
    geojson = getattr(item, "geojson", None)
    if callable(geojson):
        value = geojson()
        if isinstance(value, Mapping):
            return value
    raise TypeError("ASF Sentinel-1 result must be a mapping or expose geojson()")


def _properties(record: Mapping[str, object]) -> Mapping[str, object]:
    value = record.get("properties", record)
    if not isinstance(value, Mapping):
        raise ValueError("Sentinel-1 search record properties must be a mapping")
    return value


def _identity(properties: Mapping[str, object]) -> str:
    for key in ("sceneName", "fileID", "fileName"):
        value = properties.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "<unknown-product>"


def _required(properties: Mapping[str, object], identity: str, *names: str) -> object:
    for name in names:
        value = properties.get(name)
        if value is not None and value != "":
            return value
    raise ValueError(f"Sentinel-1 product {identity} missing required property {names[0]}")


def _text(properties: Mapping[str, object], identity: str, *names: str) -> str:
    value = _required(properties, identity, *names)
    result = str(value).strip()
    if not result:
        raise ValueError(f"Sentinel-1 product {identity} property {names[0]} is empty")
    return result


def _positive_integer(properties: Mapping[str, object], identity: str, *names: str) -> int:
    value = _required(properties, identity, *names)
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Sentinel-1 product {identity} property {names[0]} must be an integer"
        ) from error
    if result < 1:
        raise ValueError(f"Sentinel-1 product {identity} property {names[0]} must be positive")
    return result


def _time(properties: Mapping[str, object], identity: str, *names: str) -> str:
    value = _required(properties, identity, *names)
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Sentinel-1 product {identity} property {names[0]} must be ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _polarizations(properties: Mapping[str, object], identity: str) -> tuple[str, ...]:
    value = _required(properties, identity, "polarization", "polarizations")
    raw = value if isinstance(value, Sequence) and not isinstance(value, str) else re.split(
        r"[,+_/\s]+", str(value)
    )
    result = tuple(sorted({str(item).strip().upper() for item in raw if str(item).strip()}))
    if not result or not set(result).issubset({"HH", "HV", "VH", "VV"}):
        raise ValueError(f"Sentinel-1 product {identity} has invalid polarization")
    return result


def _product_type(properties: Mapping[str, object], identity: str) -> Sentinel1ProductType:
    value = _text(properties, identity, "processingLevel").upper()
    if value == "BURST":
        return Sentinel1ProductType.BURST
    if "SLC" in value:
        return Sentinel1ProductType.SLC
    if "GRD" in value:
        return Sentinel1ProductType.GRD
    raise ValueError(f"Sentinel-1 product {identity} has unsupported processingLevel {value}")


def _normalize(
    record: Mapping[str, object], expected: Sentinel1ProductType
) -> Sentinel1Product:
    properties = _properties(record)
    identity = _identity(properties)
    product_type = _product_type(properties, identity)
    if product_type is not expected:
        raise ValueError(
            f"Sentinel-1 product {identity} is {product_type.value}, expected {expected.value}"
        )
    geometry = record.get("geometry")
    try:
        footprint = shape(geometry) if isinstance(geometry, Mapping) else None
    except (TypeError, ValueError) as error:
        raise ValueError(f"Sentinel-1 product {identity} has invalid footprint") from error
    if (
        footprint is None
        or footprint.geom_type not in {"Polygon", "MultiPolygon"}
        or footprint.is_empty
        or not footprint.is_valid
    ):
        raise ValueError(f"Sentinel-1 product {identity} has invalid polygon footprint")
    start = _time(properties, identity, "startTime")
    end = _time(properties, identity, "stopTime", "endTime")
    if end < start:
        raise ValueError(f"Sentinel-1 product {identity} ends before it starts")
    direction = _text(properties, identity, "flightDirection", "orbitDirection").upper()
    if direction not in {"ASCENDING", "DESCENDING"}:
        raise ValueError(f"Sentinel-1 product {identity} has invalid flightDirection")
    size = _positive_integer(properties, identity, "bytes", "sizeBytes")
    burst = properties.get("burst")
    if product_type is Sentinel1ProductType.BURST and not isinstance(burst, Mapping):
        raise ValueError(f"Sentinel-1 product {identity} missing required property burst")
    burst = burst if isinstance(burst, Mapping) else {}

    def burst_integer(name: str) -> int | None:
        if product_type is not Sentinel1ProductType.BURST:
            return None
        return _positive_integer(burst, identity, name)

    def burst_text(*names: str) -> str | None:
        if product_type is not Sentinel1ProductType.BURST:
            return None
        return _text(burst, identity, *names).upper()

    return Sentinel1Product(
        product_id=identity,
        product_type=product_type,
        acquisition_date=start[:10],
        start_time=start,
        end_time=end,
        relative_orbit=_positive_integer(properties, identity, "pathNumber", "relativeOrbit"),
        absolute_orbit=_positive_integer(properties, identity, "orbit", "absoluteOrbit"),
        orbit_direction=direction,
        beam_mode=_text(properties, identity, "beamModeType", "beamMode").upper(),
        polarizations=_polarizations(properties, identity),
        footprint_wkt=wkt.dumps(footprint, rounding_precision=12, trim=True),
        file_name=_text(properties, identity, "fileName"),
        size_bytes=size,
        url=_text(properties, identity, "url", "downloadUrl"),
        swath=burst_text("subswath", "swath"),
        burst_index=burst_integer("burstIndex"),
        relative_burst_id=burst_integer("relativeBurstID"),
        full_burst_id=burst_text("fullBurstID"),
        merge_group=(
            str(properties.get("groupID", burst.get("groupId", ""))).strip() or None
        ),
    )


def search_sentinel1(
    case: Sentinel1AcquisitionCase, client: Sentinel1SearchClient
) -> tuple[Sentinel1Product, ...]:
    """Search, normalize, de-duplicate, and deterministically sort products."""

    products = tuple(
        _normalize(_as_mapping(record), case.product_type) for record in client.search(case)
    )
    seen: set[str] = set()
    for product in products:
        if product.product_id in seen:
            raise ValueError(f"duplicate Sentinel-1 product {product.product_id}")
        seen.add(product.product_id)
    return tuple(
        sorted(
            products,
            key=lambda item: (
                item.start_time,
                item.relative_orbit,
                item.swath or "",
                item.relative_burst_id or 0,
                item.product_id,
            ),
        )
    )


__all__ = ["AsfSentinel1SearchClient", "Sentinel1SearchClient", "search_sentinel1"]
