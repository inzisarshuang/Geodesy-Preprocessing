# 预处理路线选择

先看手中的产品类型，再选择路线。流程名称按产品状态命名，不使用需要记忆的步骤编号。

| 输入 | 推荐流程 | 预处理终点 |
|---|---|---|
| Sentinel-1 BURST 或完整 SLC | [Sentinel-1 TOPS](sentinel1_tops.md) | 按研究区裁剪的 RSLC 栈 |
| ALOS-2、COSMO、LT-1、RADARSAT-2、SAOCOM 原始产品 | [radar-grid SAR](radar_grid.md) | 按研究区裁剪的 RSLC 栈 |
| NISAR L1 RSLC HDF5 | [NISAR RSLC](nisar_rslc.md) | LA/LB 各自的裁剪 RSLC 栈 |
| NISAR L2 GSLC HDF5 | [NISAR GSLC](nisar_gslc.md) | LA/LB 各自的裁剪 GSLC 栈 |
| GeoTIFF DEM 或研究区 KML | [DEM](dem.md) | 公共 GeoTIFF，及可选 GAMMA DEM |

## 所有路线共同遵守的规则

- 在研究项目中复制并填写 CFG，不直接修改仓库模板。
- 下载或修改大批数据前先用 `--dry-run`、`--query` 或 `--plan-only` 检查计划。
- 一个轨道、方向、频段或独立网格使用一个 case；不要把不兼容的影像放进同一个栈。
- 终端显示逐景进度；Workflow 的完整输出写入项目 `LOG/PRE/`。
- 完整且输入摘要一致的结果可以安全跳过；残缺或来源变化的结果会停止，不会静默覆盖。
- 预处理到裁剪后的 RSLC/GSLC 栈结束，再把 `registered_stack.json` 交给
  `Geodesy-Analysis`。

Earthdata 账号不写进 CFG。下载时只读取 `~/.netrc`，或运行终端中的
`EARTHDATA_TOKEN`，也支持成对的 `EARTHDATA_USERNAME` 和 `EARTHDATA_PASSWORD`
环境变量。OpenTopography 密钥只从 `OPENTOPO_API_KEY` 读取。
