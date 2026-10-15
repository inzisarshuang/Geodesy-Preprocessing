# NISAR L1 RSLC 流程

NISAR RSLC 已经聚焦，但仍是雷达坐标复数产品。它需要先导入 GAMMA SLC，再走普通
radar-grid 参考几何、配准和裁剪。Frequency A（LA）是默认主链；Frequency B（LB）
必须作为独立 case 处理，不能复用 LA 的网格或 lookup table。

以下 LA 示例把 CFG 放在
`PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/`，并令每份 CFG 的
`case_root = NISAR/TRACK087_FRAME022_LA`。LB 使用自己的 case 根。

## 查询和下载

```bash
geodesy-pre-sar-nisar-acquire --config PROJECT/config/preprocessing/nisar_rslc_download.cfg --case main --query
geodesy-pre-sar-nisar-acquire --config PROJECT/config/preprocessing/nisar_rslc_download.cfg --case main --download
```

先人工检查覆盖图和筛选 CSV。Earthdata 凭据只从 `~/.netrc` 或环境变量读取。完成时
每个选中产品都有结构有效的 `.h5`，没有遗留同名 `.part`，并生成无敏感信息的
`source_manifest.json`。

## 导入 Frequency A

```bash
geodesy-pre-gamma-sar-slc --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_slc.cfg --case nisar_la --dry-run
geodesy-pre-gamma-sar-slc --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_slc.cfg --case nisar_la
```

该阶段调用 `par_NISAR_RSLC`，按 CFG 选择极化，发布 GAMMA SLC 和日期清单。

## 建立参考几何

先完成[DEM 流程](dem.md)，再运行：

```bash
geodesy-pre-gamma-sar-geo-ref --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_geo_ref.cfg --case nisar_la --dry-run
geodesy-pre-gamma-sar-geo-ref --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_geo_ref.cfg --case nisar_la
```

## 配准完整 RSLC 栈

```bash
geodesy-pre-gamma-sar-coreg --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_coreg.cfg --case nisar_la --dry-run
geodesy-pre-gamma-sar-coreg --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_coreg.cfg --case nisar_la
```

## 按 KML 缓冲范围裁剪

```bash
geodesy-pre-gamma-sar-rslc-crop --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_rslc_crop.cfg --case nisar_la --plan-only
geodesy-pre-gamma-sar-rslc-crop --config PROJECT/NISAR/TRACK087_FRAME022_LA/config/preprocessing/gamma_sar_rslc_crop.cfg --case nisar_la
```

完整结果包含 `registered_stack.json`、`images`、`crop_window.json` 和全部日期/极化。
Workflow 日志进入 `PROJECT/NISAR/TRACK087_FRAME022_LA/LOG/PRE/GAMMA/`；失败的 GAMMA 命令日志保存在输出同级的
`*.failed-logs-*`。

## 可选 Frequency B 与分析交接

需要 LB 时，把以上 GAMMA 命令的 case 改为 `nisar_lb` 并使用独立目录。预处理只发布
LA/LB 两个完整栈及其关系。双频电离层改正、干涉、解缠和时序属于
`Geodesy-Analysis`。
