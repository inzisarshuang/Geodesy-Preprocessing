# 预处理架构

## 仓库边界

`Geodesy-Preprocessing` 从外部/原始观测开始，到可供后续算法读取的已校验复杂影像
栈结束。它拥有数据获取、DEM 准备、SLC 生成或导入、参考几何、配准、产品校验和
预处理进度事件。

生成干涉图、相干性、解缠结果、形变时序、MintPy 或 DS-InSAR 产品属于
`Geodesy-Analysis`。对 radar-grid SAR 而言，发布按研究区裁剪并验证的注册 RSLC 栈
是当前预处理终点；GSLC 则发布对齐的地理网格栈。

## 源码目录的含义

`src/` 是可安装 Python 源码与测试、文档和配置模板的隔离层；
`src/geodesy_preprocessing/` 是安装后的 Python 包名。两层不是两套业务
分类，不应删除或合并。

包内先按数据领域分为 `dem/` 和 `sar/`。SAR 下面再按产品状态和处理
动作命名；不在目录名中加流程编号，因为 RSLC 与 GSLC 是分支路线，
处理顺序由流程说明和 Workflow 依赖表达。

## 目标目录：先按阶段，再按软件和数据形态拆分

目录采用以下顺序：

```text
领域（sar/dem）
  → 阶段（acquisition/slc_generation/rslc_registration/rslc_subsetting）
    → 后端（gamma/isce3/将来的独立算法）
      → 数据形态或传感器特性（tops/radar_grid）
```

这样设计有三个直接作用：

1. 下载 DEM 这类公共功能不被错误标成 GAMMA；
2. GAMMA、ISCE3 和自行开发的方法各自维护命令、参数和输出，不相互污染；
3. 卫星只在确实需要不同读取方法时拆分。生成兼容雷达网格 SLC 后，多种卫星可以
   进入同一类配准实现；TOPS 和地理编码复数网格仍保持独立。

目标 SAR 树为：

```text
sar/
├── common/                         # 后端中立的 ROI、栈清单和数据模型
├── inventory/                      # 日期和影像清单，不属于处理顺序
├── acquisition/                    # 查询、覆盖分析、人工选择和下载
│   ├── sentinel1/
│   └── nisar/
├── slc_generation/                 # 供应商/任务产品生成或导入 SLC
│   └── gamma/
│       ├── tops/
│       └── radar_grid/adapters/
├── rslc_registration/              # SLC 建立参考几何并配准为 RSLC
│   ├── common/
│   └── gamma/
│       ├── tops/
│       └── radar_grid/
├── rslc_subsetting/                # 按研究区裁剪注册 RSLC 栈
│   └── gamma/radar_grid/
└── gslc_subsetting/                # 校验并裁剪已地理编码的 GSLC 栈
    └── nisar/
```

`other_sar` 不是正式数据类别，顶层 `sentinel1/` 也不是正式的新架构。
已迁移源文件后仅由 `__pycache__` 留下的空壳目录必须清理，不得继续
出现在交付树中。

## 当前所有权

| 阶段 | 正式代码位置 | 状态 |
|---|---|---|
| OpenTopography DEM 下载 | `dem/acquisition/opentopography` | 公共实现 |
| GAMMA DEM 转换 | `dem/conversion/gamma` | 已迁移 |
| Sentinel-1 BURST/SLC/GRD 查询、下载、BURST→SAFE | `sar/acquisition/sentinel1` | 正式入口已实现，真实查询通过，真实下载待复验 |
| GAMMA TOPS SLC | `sar/slc_generation/gamma/tops` | 已迁移 |
| GAMMA 雷达网格 SLC | `sar/slc_generation/gamma/radar_grid` | 已迁移 |
| GAMMA TOPS 参考几何/配准 | `sar/rslc_registration/gamma/tops` | 正式实现 |
| GAMMA 注册 RSLC KML 裁剪 | `sar/rslc_subsetting/gamma/radar_grid` | 正式实现，真实窗口抽查通过 |
| GAMMA 普通雷达网格参考几何/配准 | `sar/rslc_registration/gamma/radar_grid` | 正式实现，真实数值待验收 |
| NISAR L1 RSLC 查询、覆盖与下载 | `sar/acquisition/nisar` | 已实现，真实数据待验收 |
| NISAR RSLC → GAMMA SLC | `sar/slc_generation/gamma/radar_grid/adapters/nisar.py` | 已实现，真实数据待验收 |
| NISAR LA/LB 栈关系 | `sar/common/frequency_bundle.py` | 已实现 |
| NISAR L2 GSLC 网格校验与裁剪 | `sar/gslc_subsetting/nisar` | 正式实现，LA/HH 真实数据验收通过 |
| NISAR L0B 聚焦 | 尚无正式实现 | 后续独立计划 |

历史 `sentinel1`、`other_sar` Python 兼容模块已经删除。GAMMA-InSAR shell 文件暂时
仅负责参数转发，不得重新出现循环、GAMMA 数值命令、删除操作或 Conda 激活逻辑。

## 稳定边界

后端内部可以使用自己的参数对象，但跨组件只发布后端中立描述：参考日期、有序日期、
数据几何、原生产品根目录、质量记录和清单路径。Analysis/Adapter 根据清单判断兼容，
不能只根据目录名猜测。

## 使用说明分层

说明只分两类，不使用需要记忆的流程编号：

1. **CFG 单阶段说明**：每份模板说明本阶段的用途、前置输入、命令、参数、
   输出、日志、完成判断、安全重跑和下一阶段。
2. **流程说明**：一份总流程帮助用户按产品类型选择路线；再分别提供
   DEM、Sentinel-1 TOPS、普通 radar-grid、NISAR RSLC 和 NISAR GSLC 的完整
   可执行流程。

总路线为：

```text
Sentinel-1 TOPS:
acquisition/sentinel1
  → slc_generation/gamma/tops
  → rslc_registration/gamma/tops
  → rslc_subsetting/gamma/radar_grid

普通 radar-grid 与 NISAR RSLC:
acquisition 或本地产品
  → slc_generation/gamma/radar_grid
  → rslc_registration/gamma/radar_grid
  → rslc_subsetting/gamma/radar_grid

NISAR GSLC:
acquisition/nisar
  → gslc_subsetting/nisar
```

## 项目与日志

代码仓库只保存通用实现和空白模板。研究区 KML、已填写 CFG、账号引用、输入产品、
运行结果和日志均保留在研究项目目录。直接运行显示统一进度；Workflow 将完整输出写入
`项目/LOG/PRE/<后端>/<任务>/<运行时间>/`。

Git 记录代码演进；项目 LOG 记录某次数据处理。二者用途不同，不能相互替代。

## Sentinel-1 获取与 GAMMA 的交界

`sar/acquisition/sentinel1` 是公共层，不导入 GAMMA。它发布
`query_dir/images_NNN`、`download_dir/Orbit_NNN`、`query_manifest.json`、
`source_manifest.json` 和（BURST 情况）`safe_manifest.json`。项目中的
`gamma_sar_tops_slc.cfg` 明确把 `raw_dir` 指向该 `Orbit_NNN`，把 `images` 指向
查询目录的 `images_NNN`；参考 burst 表仍由独立的 burst 表步骤生成。SLC/GRD 不执行
BURST 合并，GRD 也不进入当前 TOPS InSAR 链。

## NISAR 边界

NISAR 获取代码是公共层：它只负责查询、画覆盖图、筛选和下载只读 HDF5，不依赖
GAMMA。`par_NISAR_RSLC` 适配器位于 GAMMA 后端，把所选频率和极化导入为普通雷达
坐标 SLC；之后复用 GAMMA radar-grid 的参考几何、配准和 KML 裁剪。

Frequency A 是默认主链。Frequency B 必须显式获取和单独处理，LA/LB 允许具有不同
尺寸和像元间距。预处理只发布两个已校验栈及其关系；真正的双频电离层计算属于
`Geodesy-Analysis`。

GSLC 是另一条独立预处理路径。公共获取层仍负责查询、覆盖图和下载，但下载后进入
`sar/gslc_subsetting/nisar`：只读检查 HDF5 身份与地图网格，要求多日期 CRS、带符号
间距、原点和形状完全一致，再按 KML 缓冲区分块提取 complex64 GeoTIFF。该模块不
导入 GAMMA 后端，也不调用任何 GAMMA 命令。LA 与 LB 各自发布
`geometry_type = geocoded_grid` 的完整栈；之后的干涉和双频电离层算法属于 Analysis。
