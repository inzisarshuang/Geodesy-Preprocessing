"""NISAR ASF 查询适配与后端中立结果转换。

功能概述:
    把 ``asf_search`` 产品对象隔离在获取边界，并将普通 GeoJSON 映射转换为稳定、
    不含凭据的 ``NisarGranule`` 元数据。

函数说明:
    ``NisarSearchClient`` 定义可注入查询接口；``AsfSearchClient`` 调用 ASF 查询；
    ``search_nisar`` 校验、去重并确定性排列查询结果。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from shapely import wkt
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from geodesy_preprocessing.sar.common.roi import load_kml_roi

from .models import NisarGranule, NisarProductType, NisarSearchCase


@runtime_checkable
class NisarSearchClient(Protocol):
    """Provider boundary returning GeoJSON-like mappings only."""

    def search(self, case: NisarSearchCase) -> Sequence[Mapping[str, object]]:
        """Search one case without downloading any product bytes."""


class AsfSearchClient:
    """Lazy ASF adapter that converts product objects to plain mappings."""

    def search(self, case: NisarSearchCase) -> tuple[Mapping[str, object], ...]:
        """Run one unauthenticated ASF metadata query for a NISAR RSLC or GSLC case."""

        import asf_search as asf

        aoi = load_kml_roi(case.kml_file)
        options: dict[str, object] = {
            "dataset": [asf.constants.DATASET.NISAR],
            "processingLevel": [case.product_type.value],
            "intersectsWith": wkt.dumps(aoi, rounding_precision=12, trim=True),
            "start": case.start_date,
            "end": case.end_date,
        }
        if case.tracks is not None:
            options["relativeOrbit"] = list(case.tracks)
        if case.frames is not None:
            options["frame"] = list(case.frames)
        if case.orbit_direction is not None:
            options["flightDirection"] = case.orbit_direction
        results = asf.geo_search(**options)
        return tuple(_as_mapping(item) for item in results)


def _as_mapping(item: object) -> Mapping[str, object]:
    if isinstance(item, Mapping):
        return item
    geojson = getattr(item, "geojson", None)
    if callable(geojson):
        value = geojson()
        if isinstance(value, Mapping):
            return value
    raise TypeError("ASF search result must be a mapping or expose geojson()")


def _properties(record: Mapping[str, object]) -> Mapping[str, object]:
    value = record.get("properties", record)
    if not isinstance(value, Mapping):
        raise ValueError("NISAR search record properties must be a mapping")
    return value


def _required(properties: Mapping[str, object], *names: str) -> object:
    for name in names:
        value = properties.get(name)
        if value is not None and value != "":
            return value
    raise ValueError(f"NISAR search record missing required property {names[0]}")


def _text(properties: Mapping[str, object], *names: str) -> str:
    value = _required(properties, *names)
    if not isinstance(value, (str, int)):
        raise ValueError(f"NISAR property {names[0]} must be text")
    result = str(value).strip()
    if not result:
        raise ValueError(f"NISAR property {names[0]} must not be empty")
    return result


def _positive_integer(properties: Mapping[str, object], *names: str) -> int:
    value = _required(properties, *names)
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"NISAR property {names[0]} must be an integer") from error
    if result < 1:
        raise ValueError(f"NISAR property {names[0]} must be positive")
    return result


def _time(properties: Mapping[str, object], *names: str) -> str:
    value = _required(properties, *names)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"NISAR property {names[0]} must be ISO-8601") from error
    else:
        raise ValueError(f"NISAR property {names[0]} must be ISO-8601")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _tokens(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    raw: Sequence[object]
    if isinstance(value, str):
        raw = tuple(part for part in re.split(r"[,+_\s]+", value) if part)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw = value
    else:
        raise ValueError(f"NISAR property {label} must be text or a list")
    result: list[str] = []
    for item in raw:
        token = str(item).strip().upper()
        if token and token not in result:
            result.append(token)
    return tuple(result)


def _frequency_and_polarization(
    properties: Mapping[str, object],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    direct_frequencies = _tokens(properties.get("frequencies"), "frequencies")
    direct_polarizations = _tokens(properties.get("polarizations"), "polarizations")
    main = _tokens(properties.get("mainBandPolarization"), "mainBandPolarization")
    auxiliary = _tokens(properties.get("sideBandPolarization"), "sideBandPolarization")
    if direct_frequencies:
        frequencies = direct_frequencies
    else:
        if not main:
            raise ValueError("NISAR search record missing required property mainBandPolarization")
        frequencies = ("A",) + (("B",) if auxiliary else ())
    polarizations = direct_polarizations or tuple(dict.fromkeys(main + auxiliary))
    if not polarizations:
        raise ValueError("NISAR search record missing required property polarizations")
    unsupported_frequency = sorted(set(frequencies) - {"A", "B"})
    unsupported_polarization = sorted(set(polarizations) - {"HH", "HV", "VH", "VV"})
    if unsupported_frequency:
        raise ValueError(f"unsupported NISAR frequency {unsupported_frequency[0]}")
    if unsupported_polarization:
        raise ValueError(f"unsupported NISAR polarization {unsupported_polarization[0]}")
    return tuple(sorted(frequencies)), tuple(sorted(polarizations))


def _size_bytes(properties: Mapping[str, object], url: str) -> int:
    value = _required(properties, "bytes", "sizeBytes")
    if isinstance(value, Mapping):
        filename = Path(urlparse(url).path).name
        entry = value.get(filename)
        if entry is None:
            hdf5_entries = [
                candidate
                for name, candidate in value.items()
                if str(name).lower().endswith((".h5", ".hdf5"))
            ]
            if len(hdf5_entries) != 1:
                raise ValueError("NISAR bytes metadata does not identify one HDF5 product")
            entry = hdf5_entries[0]
        value = entry.get("bytes") if isinstance(entry, Mapping) else entry
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("NISAR property bytes must contain an integer size") from error
    if result < 1:
        raise ValueError("NISAR property bytes must be positive")
    return result


def _footprint(record: Mapping[str, object]) -> BaseGeometry:
    geometry = record.get("geometry")
    if not isinstance(geometry, Mapping):
        raise ValueError("NISAR search record missing required geometry")
    try:
        footprint = shape(geometry)
    except (TypeError, ValueError) as error:
        raise ValueError("NISAR search record has invalid geometry") from error
    if footprint.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("NISAR footprint must be a Polygon or MultiPolygon")
    if footprint.is_empty or not footprint.is_valid:
        raise ValueError("NISAR footprint must be a valid non-empty polygon")
    return footprint


def _normalize(
    record: Mapping[str, object], expected_product_type: NisarProductType
) -> NisarGranule:
    properties = _properties(record)
    granule_id = _text(properties, "sceneName", "granuleName", "fileID")
    product_value = properties.get("processingLevel", expected_product_type.value)
    try:
        product_type = NisarProductType(str(product_value).strip().upper())
    except ValueError as error:
        raise ValueError(
            f"NISAR granule {granule_id} has unsupported processingLevel {product_value}"
        ) from error
    if product_type is not expected_product_type:
        raise ValueError(
            f"NISAR granule {granule_id} is {product_type.value}, "
            f"expected {expected_product_type.value}"
        )
    start_time = _time(properties, "startTime")
    end_time = _time(properties, "stopTime", "endTime")
    if end_time < start_time:
        raise ValueError(f"NISAR granule {granule_id} ends before it starts")
    direction = _text(properties, "flightDirection", "orbitDirection").upper()
    if direction not in {"ASCENDING", "DESCENDING"}:
        raise ValueError(f"NISAR property flightDirection is invalid: {direction}")
    url = _text(properties, "url", "downloadUrl")
    frequencies, polarizations = _frequency_and_polarization(properties)
    checksum_value = properties.get("checksum")
    if checksum_value is None and properties.get("md5sum"):
        checksum_value = f"md5:{properties['md5sum']}"
    checksum = None if checksum_value is None else str(checksum_value).strip() or None
    footprint = _footprint(record)
    return NisarGranule(
        granule_id=granule_id,
        product_type=product_type,
        acquisition_date=start_time[:10],
        start_time=start_time,
        end_time=end_time,
        track=_positive_integer(properties, "pathNumber", "track"),
        frame=_positive_integer(properties, "frameNumber", "frame"),
        orbit_direction=direction,
        footprint_wkt=wkt.dumps(footprint, rounding_precision=12, trim=True),
        size_bytes=_size_bytes(properties, url),
        url=url,
        frequencies=frequencies,
        polarizations=polarizations,
        checksum=checksum,
    )


def search_nisar(
    case: NisarSearchCase, client: NisarSearchClient
) -> tuple[NisarGranule, ...]:
    """Search, normalize, reject duplicate identities, and sort NISAR granules."""

    normalized = tuple(
        _normalize(_as_mapping(record), case.product_type) for record in client.search(case)
    )
    seen: set[str] = set()
    for granule in normalized:
        if granule.granule_id in seen:
            raise ValueError(f"duplicate NISAR granule {granule.granule_id}")
        seen.add(granule.granule_id)
    return tuple(
        sorted(
            normalized,
            key=lambda item: (item.start_time, item.track, item.frame, item.granule_id),
        )
    )


__all__ = ["AsfSearchClient", "NisarSearchClient", "search_nisar"]
