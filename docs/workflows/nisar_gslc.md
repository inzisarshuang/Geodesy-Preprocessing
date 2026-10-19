# NISAR L2 GSLC 流程

GSLC 已位于地图网格，不需要 GAMMA 配准。Native 路线检查多日期网格一致性，按 KML
缓冲范围提取 complex64 数据，并发布 geocoded-grid 栈。它与 NISAR RSLC 路线独立。

## 查询和下载

```bash
geodesy-pre-sar-nisar-acquire --config PROJECT/config/acquisition/nisar_gslc_download.cfg --case main --query
geodesy-pre-sar-nisar-acquire --config PROJECT/config/acquisition/nisar_gslc_download.cfg --case main --download
```

人工检查覆盖图和筛选 CSV 后再下载。Earthdata 凭据只从 `~/.netrc` 或环境变量读取；
CFG 不保存账号。完成时应有 `.h5` 和 `source_manifest.json`，且没有 `.part`。

## 检查网格和裁剪 LA

```bash
geodesy-pre-sar-nisar-gslc --config PROJECT/config/preprocessing/nisar_gslc_stack.cfg --case la --plan-only
geodesy-pre-sar-nisar-gslc --config PROJECT/config/preprocessing/nisar_gslc_stack.cfg --case la
```

`--plan-only` 必须确认 CRS、带符号像元间距、原点、形状、日期和极化一致。正式运行
不会重投影或重采样复数相位。输出包含 `registered_stack.json`、`crop_window.json`、
`quality.json`、complex64 GeoTIFF、元数据和 quicklook。

## 可选 LB 和交接

需要 Frequency B 时使用独立的 `--case lb`。LA 与 LB 可能具有不同尺寸和像元间距，
不得强制共用裁剪窗口。最终 `registered_stack.json` 交给 `Geodesy-Analysis`；后续可从
GSLC 形成 GUNW 或进入其他干涉/时序方法，但不由本仓库执行。

Workflow 日志位于 `PROJECT/LOG/PRE/NISAR/GSLC/`。相同输入安全跳过；来源或网格改变
时停止，不覆盖旧正式结果。
