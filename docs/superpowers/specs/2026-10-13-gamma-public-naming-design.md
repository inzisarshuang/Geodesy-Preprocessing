# GAMMA 预处理公开命名设计

## 目标

统一 GAMMA SAR 预处理的公开命令和 CFG 名称，使名称固定采用：

```text
gamma_<功能>_<数据类型>
```

数据类型只使用一个词：

- `tops`：TOPS 数据链路，目前主要用于 Sentinel-1。
- `grid`：普通雷达坐标格网数据链路，用于 SAOCOM、NISAR、ALOS-2、LT-1 等。

该规则只调整用户可见名称。Python 内部仍使用技术含义更准确的 `tops` 和
`radar_grid` 目录，不把内部模块改成含义较宽的 `sar`。

## 公开名称

| 功能 | TOPS | 普通雷达格网 |
|---|---|---|
| 生成 SLC | `gamma_slc_tops` | `gamma_slc_grid` |
| 建立 DEM 参考几何 | `gamma_geo_ref_tops` | `gamma_geo_ref_grid` |
| 配准生成 RSLC | `gamma_coreg_tops` | `gamma_coreg_grid` |

CFG 文件使用下划线：

```text
gamma_slc_tops.cfg
gamma_geo_ref_tops.cfg
gamma_coreg_tops.cfg
gamma_slc_grid.cfg
gamma_geo_ref_grid.cfg
gamma_coreg_grid.cfg
```

命令使用连字符，并保留仓库统一的 `geodesy-pre-` 前缀：

```text
geodesy-pre-gamma-slc-tops
geodesy-pre-gamma-geo-ref-tops
geodesy-pre-gamma-coreg-tops
geodesy-pre-gamma-slc-grid
geodesy-pre-gamma-geo-ref-grid
geodesy-pre-gamma-coreg-grid
```

`gamma_rslc_crop.cfg` 和 `geodesy-pre-gamma-rslc-crop` 继续作为两条链路共用的
裁剪入口，因此不增加 `tops` 或 `grid` 尾缀。DEM 下载、DEM 转换和数据查询等
公共功能也不增加这两个尾缀。

## 现有名称迁移

| 现有 CFG | 新 CFG |
|---|---|
| `gamma_sar_tops_slc.cfg` | `gamma_slc_tops.cfg` |
| `gamma_sar_tops_geo_ref.cfg` | `gamma_geo_ref_tops.cfg` |
| `gamma_sar_tops_coreg.cfg` | `gamma_coreg_tops.cfg` |
| `gamma_sar_slc.cfg` | `gamma_slc_grid.cfg` |
| `gamma_sar_geo_ref.cfg` | `gamma_geo_ref_grid.cfg` |
| `gamma_sar_coreg.cfg` | `gamma_coreg_grid.cfg` |
| `gamma_sar_rslc_crop.cfg` | `gamma_rslc_crop.cfg` |

命令按相同顺序迁移，例如：

```text
geodesy-pre-gamma-sar-tops-slc -> geodesy-pre-gamma-slc-tops
geodesy-pre-gamma-sar-coreg    -> geodesy-pre-gamma-coreg-grid
```

项目目录通过 `[case:...]` 和 `sensor = ...` 区分卫星，文件名不再重复附加
`saocom`、`nisar` 等卫星名称。一个项目需要多个同类配置时，可以用 case 区分；
确实需要拆文件时，将项目用途放在 `grid` 或 `tops` 之前，保证类型仍是尾缀。

## 实施边界

本次改名覆盖：

1. `pyproject.toml` 中的公开命令。
2. `config_examples` 中的 CFG 文件名及其调用示例。
3. README、工作流文档和架构说明中的名称及链接。
4. 测试中对公开命令和示例文件名的检查。
5. 当前测试项目内已经生成的预处理 CFG 名称及文件头调用示例。

不调整 Python 包内部的领域目录，不改变参数含义，不改变 GAMMA 命令参数，也不
重新运行已经完成的数据处理。

## 收敛方式

实施期间先建立并验证全部新入口；新入口通过命令发现、配置解析和现有单元测试后，
在同一次改动中删除旧入口。最终发布状态不长期保留旧命令别名，也不同时保存两套
示例 CFG，避免用户继续看到重复入口。

## 验收标准

1. 六个新命令均能显示 `--help`，并指向原有对应功能。
2. 六个新示例 CFG 均能被对应命令解析。
3. TOPS 与 grid 的现有单元测试全部通过。
4. README 和工作流只展示新名称。
5. 除本迁移说明外，全仓库不再出现被替换的旧公开名称。
6. 项目 CFG 的数据路径、case 名和处理参数在改名前后保持一致。
