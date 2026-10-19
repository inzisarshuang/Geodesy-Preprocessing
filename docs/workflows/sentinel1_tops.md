# Sentinel-1 TOPS 流程

这条路线从 ASF 查询开始，到按研究区裁剪的注册 RSLC 栈结束。GRD 可以查询和下载，
但不进入当前 TOPS InSAR 路线。

## 查询、人工选择和下载

填写 `sentinel1_download.cfg` 的研究区、日期、产品类型、极化、轨道和方向。先查询并
检查覆盖图、CSV 与 `images_NNN`，确认后再下载。BURST 产品下载后还要合并 SAFE。

```bash
geodesy-pre-sar-sentinel1-acquire --config PROJECT/config/acquisition/sentinel1_download.cfg --case main --query
geodesy-pre-sar-sentinel1-acquire --config PROJECT/config/acquisition/sentinel1_download.cfg --case main --download
geodesy-pre-sar-sentinel1-acquire --config PROJECT/config/acquisition/sentinel1_download.cfg --case main --merge
```

Earthdata 凭据仅从 `~/.netrc` 或环境变量读取。完整 SLC 不运行 `--merge`；BURST 完成
后应有 `source_manifest.json`、`safe_manifest.json` 和结构完整的 SAFE。

## SAFE 生成 GAMMA SLC

让 `gamma_slc_tops.cfg` 的 `raw_dir` 指向下载轨道目录，让本阶段的影像清单指向
查询生成的 `images_NNN`，并填写 `roi_file`、`reference_selector` 和
`burst_table`。`reference_selector` 通常就是参考日期（YYYYMMDD）。

如果 `burst_table` 尚不存在，命令会先从该日期的 SAFE 自动生成参考 burst 表；同一
日期被拆成多个 SAFE 时也会一起处理。已有非空表则直接复用，所以无需再手动执行旧的
`S1-02get_burst_table`。自动生成目前用于 SAFE 流程；传统 ZIP 流程仍需提供现成表。

```bash
geodesy-pre-gamma-slc-tops --config PROJECT/config/preprocessing/gamma_slc_tops.cfg --case main
```

参考表和 `burst_itab_*` 保存在 `burst_table` 指定目录；如果
`burst_coverage_plot = true`，同目录还会保存覆盖检查图。随后才开始逐景生成 SLC。

## 建立参考几何并配准

先按[DEM 流程](dem.md)准备 GAMMA DEM，再运行：

```bash
geodesy-pre-gamma-geo-ref-tops --config PROJECT/config/preprocessing/gamma_geo_ref_tops.cfg --case main
geodesy-pre-gamma-coreg-tops --config PROJECT/config/preprocessing/gamma_coreg_tops.cfg --case main
```

参考日期必须存在于 SLC 栈。配准完成后检查 RSLC 日期数、质量文件和失败日志。

## 按研究区裁剪

```bash
geodesy-pre-gamma-rslc-crop --config PROJECT/config/preprocessing/gamma_rslc_crop.cfg --case main --plan-only
geodesy-pre-gamma-rslc-crop --config PROJECT/config/preprocessing/gamma_rslc_crop.cfg --case main
```

先查看缓冲后的 KML 覆盖率与统一裁剪窗口。完成标志是输出目录存在可读取的
`registered_stack.json`、`images`、`crop_window.json`，并且所选日期和极化全部存在。

## 日志和交接

Workflow 日志位于 `PROJECT/LOG/PRE/`；GAMMA 逐景 stdout/stderr 也保留在阶段输出的
`logs/日期/极化/`。将最终裁剪栈的 `registered_stack.json` 交给 `Geodesy-Analysis`。
