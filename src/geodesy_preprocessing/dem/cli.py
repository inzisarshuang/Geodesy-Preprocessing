"""Installed command for OpenTopography DEM preparation.

功能概述:
    读取项目 CFG，计算范围，下载并校验 DEM，并可继续调用 GAMMA ``srtm2dem``。

函数说明:
    ``run_from_config`` 执行完整流程；``build_parser`` 创建命令参数；
    ``main`` 提供稳定的命令行退出码和面向用户的错误信息。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import (
    ALLOWED_CONFIG_KEYS,
    cfg_bool,
    cfg_float,
    cfg_int,
    cfg_str,
    load_config,
    resolve_path,
    validate_config_keys,
)
from .extent import (
    bbox_from_config,
    bbox_from_kml,
    estimate_bbox_area_km2,
    expand_bbox_km,
)
from .acquisition.opentopography import (
    DEFAULT_API_URL,
    KNOWN_30M_DATASETS,
    MAX_AREA_30M_KM2,
    OFFICIAL_OUTPUT_FORMATS,
    download_dem,
    normalize_dem_type,
    resolve_api_key,
    validate_api_url,
    validate_geotiff,
    write_metadata,
)
from .conversion.gamma.srtm2dem import convert_with_srtm2dem, validate_gamma_dem


def run_from_config(
    cfg: Mapping[str, str],
    *,
    config_path: str | Path,
    force_override: bool = False,
    dry_run_override: bool = False,
) -> Path | None:
    """Run download and optional GAMMA conversion from one configuration."""

    config_path = Path(config_path).expanduser().resolve()
    base_dir = config_path.parent
    validate_config_keys(cfg, ALLOWED_CONFIG_KEYS, config_path)

    input_mode = cfg_str(cfg, "input_mode", "kml").lower()
    kml_placemark: str | None = None
    if input_mode == "kml":
        kml_value = cfg_str(cfg, "kml_file")
        if not kml_value:
            raise ValueError("input_mode=kml requires kml_file")
        input_kml: Path | None = resolve_path(kml_value, base_dir)
        kml_placemark = cfg_str(cfg, "kml_placemark") or None
        source_bbox = bbox_from_kml(input_kml, kml_placemark)
    elif input_mode == "bbox":
        input_kml = None
        source_bbox = bbox_from_config(cfg)
    else:
        raise ValueError(f"input_mode must be 'kml' or 'bbox', got {input_mode!r}")

    buffer_km = float(cfg_float(cfg, "buffer_km", 0.0) or 0.0)
    request_bbox = expand_bbox_km(source_bbox, buffer_km)
    estimated_area_km2 = estimate_bbox_area_km2(request_bbox)

    dem_type = normalize_dem_type(cfg_str(cfg, "dem_type", "COP30"))
    api_url = validate_api_url(cfg_str(cfg, "api_url", DEFAULT_API_URL))
    output_format = cfg_str(cfg, "output_format", "GTiff")
    if output_format not in OFFICIAL_OUTPUT_FORMATS:
        raise ValueError(f"output_format must be one of {sorted(OFFICIAL_OUTPUT_FORMATS)}")

    output_dir = resolve_path(cfg_str(cfg, "output_dir", "."), base_dir)
    output_name = cfg_str(cfg, "output_name", "DEM.tif")
    if not output_name:
        raise ValueError("output_name cannot be empty")
    output_path = output_dir / output_name
    overwrite = cfg_bool(cfg, "overwrite", False) or force_override
    dry_run = cfg_bool(cfg, "dry_run", False) or dry_run_override

    source_vertical_datum = cfg_str(cfg, "source_vertical_datum", "unspecified")
    run_gamma = cfg_bool(cfg, "run_srtm2dem", False)
    gamma_command = cfg_str(cfg, "srtm2dem_command", "srtm2dem")
    gamma_dem_name = cfg_str(cfg, "srtm2dem_dem_name", "srtm.dem")
    gamma_par_name = cfg_str(cfg, "srtm2dem_par_name", "srtm.dem.par")
    if not gamma_dem_name or not gamma_par_name:
        raise ValueError("srtm2dem output names cannot be empty")
    gamma_output_value = cfg_str(cfg, "srtm2dem_output_dir")
    gamma_output_dir = (
        resolve_path(gamma_output_value, base_dir)
        if gamma_output_value
        else output_dir
    )
    gamma_dem_path = gamma_output_dir / gamma_dem_name
    gamma_par_path = gamma_output_dir / gamma_par_name
    gamma_log_name = cfg_str(cfg, "srtm2dem_log_name", "srtm2dem.log")
    gamma_log_path = gamma_output_dir / gamma_log_name if gamma_log_name else None
    gamma_geoid_flag = int(cfg_int(cfg, "srtm2dem_geoid_correction_flag", 0) or 0)
    gamma_geoid_value = cfg_str(cfg, "srtm2dem_geoid_file")
    gamma_geoid_path = resolve_path(gamma_geoid_value, base_dir) if gamma_geoid_value else None
    gamma_gdal_value = cfg_str(cfg, "srtm2dem_gdal_bin_dir")
    gamma_gdal_bin = resolve_path(gamma_gdal_value, base_dir) if gamma_gdal_value else None
    if run_gamma and output_format != "GTiff":
        raise ValueError("run_srtm2dem requires output_format=GTiff")
    if run_gamma and gamma_geoid_flag == 2:
        if gamma_geoid_path is None:
            raise ValueError("srtm2dem geoid flag 2 requires a geoid file")
        if not gamma_geoid_path.is_file():
            raise FileNotFoundError(f"geoid file not found: {gamma_geoid_path}")

    timeout_connect = float(cfg_float(cfg, "timeout_connect_sec", 30.0) or 30.0)
    timeout_read = float(cfg_float(cfg, "timeout_read_sec", 600.0) or 600.0)
    retries = int(cfg_int(cfg, "retries", 4) or 0)
    backoff = float(cfg_float(cfg, "backoff_factor", 2.0) or 0.0)
    chunk_mb = int(cfg_int(cfg, "chunk_mb", 4) or 4)
    verify_tiff = cfg_bool(cfg, "verify_tiff", True)

    print("=" * 72)
    print("OpenTopography DEM 下载与 GAMMA 转换")
    print("=" * 72)
    print(f"[DEM] 配置文件: {config_path}")
    print(f"[DEM] 输入方式: {input_mode}")
    if input_kml:
        print(f"[DEM] KML: {input_kml}")
    print(
        f"[DEM] 请求范围: W={request_bbox.west:.10f}, S={request_bbox.south:.10f}, "
        f"E={request_bbox.east:.10f}, N={request_bbox.north:.10f}"
    )
    print(f"[DEM] 估算面积: {estimated_area_km2:,.2f} km²")
    print(f"[DEM] 数据集: {dem_type}")
    print(f"[DEM] GeoTIFF: {output_path}")
    print(f"[DEM] 运行 srtm2dem: {run_gamma}")
    if run_gamma:
        print(f"[DEM] GAMMA DEM: {gamma_dem_path}")
        print(f"[DEM] GAMMA 参数: {gamma_par_path}")
        print(f"[DEM] geoid correction: {gamma_geoid_flag}")
        if gamma_geoid_path is not None:
            print(f"[DEM] geoid 文件: {gamma_geoid_path}")
        if gamma_gdal_bin is not None:
            print(f"[DEM] GDAL 目录: {gamma_gdal_bin}")
    print(f"[DEM] dry-run: {dry_run}")

    if dem_type in KNOWN_30M_DATASETS and estimated_area_km2 > MAX_AREA_30M_KM2:
        raise ValueError(
            f"estimated AOI area ({estimated_area_km2:,.0f} km²) exceeds the "
            f"~{MAX_AREA_30M_KM2:,.0f} km² request limit for 30 m DEM products; "
            "split the AOI into smaller requests"
        )
    gamma_metadata = None
    if run_gamma and not overwrite and not dry_run:
        destinations = [gamma_dem_path, gamma_par_path]
        if gamma_log_path is not None:
            destinations.append(gamma_log_path)
        existing = [path for path in destinations if path.exists()]
        if existing:
            if len(existing) != len(destinations):
                raise RuntimeError(
                    "incomplete GAMMA DEM output exists; inspect or use --force: "
                    + ", ".join(str(path) for path in existing)
                )
            gamma_metadata = validate_gamma_dem(gamma_dem_path, gamma_par_path)
            gamma_metadata["status"] = "skipped_complete"
    if dry_run:
        print("[DEM] dry-run 完成：没有读取 API 密钥，也没有发送网络请求。")
        return None

    if run_gamma and output_path.exists() and not overwrite:
        if output_path.is_symlink() or not output_path.is_file():
            raise ValueError(f"existing GeoTIFF must be a regular file: {output_path}")
        if verify_tiff:
            validate_geotiff(output_path, request_bbox)
        output = output_path
        api_key_source = "not_used_existing_geotiff"
        print(f"[DEM] GeoTIFF 已存在且校验通过，跳过下载: {output}")
    else:
        api_key, api_key_source = resolve_api_key(cfg)
        print(f"[DEM] API 密钥来源: {api_key_source}")
        output = download_dem(
            api_url=api_url,
            dem_type=dem_type,
            bbox=request_bbox,
            output_path=output_path,
            api_key=api_key,
            output_format=output_format,
            timeout_connect_sec=timeout_connect,
            timeout_read_sec=timeout_read,
            retries=retries,
            backoff_factor=backoff,
            chunk_mb=chunk_mb,
            overwrite=overwrite,
            verify_tiff=verify_tiff,
        )
        print(f"[DEM] 下载完成: {output}")

    if run_gamma:
        if gamma_metadata is None:
            gamma_metadata = convert_with_srtm2dem(
                input_geotiff=output,
                output_dem=gamma_dem_path,
                output_par=gamma_par_path,
                command=gamma_command,
                geoid_correction_flag=gamma_geoid_flag,
                geoid_path=gamma_geoid_path,
                gdal_bin_dir=gamma_gdal_bin,
                log_path=gamma_log_path,
                overwrite=overwrite,
            )
        else:
            print("[DEM] GAMMA DEM 结果完整，跳过转换。")
        print(f"[DEM] GAMMA DEM: {gamma_dem_path}")
        print(f"[DEM] GAMMA 参数: {gamma_par_path}")

    if cfg_bool(cfg, "write_metadata", True):
        metadata_name = cfg_str(cfg, "metadata_name", f"{output.name}.json")
        write_metadata(
            output_dir / metadata_name,
            config_path=config_path,
            input_mode=input_mode,
            input_kml=input_kml,
            kml_placemark=kml_placemark if input_mode == "kml" else None,
            source_bbox=source_bbox,
            request_bbox=request_bbox,
            buffer_km=buffer_km,
            estimated_area_km2=estimated_area_km2,
            api_url=api_url,
            dem_type=dem_type,
            output_format=output_format,
            output_path=output,
            api_key_source=api_key_source,
            source_vertical_datum=source_vertical_datum,
            gamma_dem=gamma_metadata,
        )
    return output


def build_parser() -> argparse.ArgumentParser:
    """Build the human-facing DEM command parser."""

    parser = argparse.ArgumentParser(
        description="按 CFG 从 OpenTopography 下载 DEM，并可转换为 GAMMA 格式。"
    )
    parser.add_argument("--config", "-c", required=True, help="DEM CFG 配置文件")
    parser.add_argument("--dry-run", action="store_true", help="只检查并显示参数，不下载")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有输出")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command and return 0 for success or 1 for an ordinary failure."""

    args = build_parser().parse_args(argv)
    try:
        config_path = Path(args.config).expanduser().resolve()
        run_from_config(
            load_config(config_path),
            config_path=config_path,
            force_override=args.force,
            dry_run_override=args.dry_run,
        )
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
