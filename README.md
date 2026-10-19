# Geodesy-Preprocessing

Geodesy-Preprocessing is a configuration-driven toolkit that turns public DEM
data and multi-sensor SAR products into validated SLC, RSLC, or GSLC stacks for
downstream geodetic analysis.

Geodesy-Preprocessing 是一个配置驱动的测地数据预处理工具库，用于将公共 DEM 和
多源 SAR 产品整理为经过校验、可以交给后续分析使用的 SLC、RSLC 或 GSLC 栈。

## 1. Repository Scope / 仓库定位

### 1.1 Inputs And Outputs / 输入与输出

- Inputs / 输入：研究区 KML 或 bbox、公共 DEM、Sentinel-1 BURST/SLC/GRD、
  ALOS-2、COSMO-SkyMed、LT-1、RADARSAT-2、SAOCOM，以及 NISAR L1 RSLC 或
  L2 GSLC。
- Intermediate products / 中间产品：GeoTIFF/GAMMA DEM、GAMMA SLC、参考几何、
  配准质量记录和逐景日志。
- Final products / 最终产品：按研究区缓冲范围裁剪并校验的 RSLC 或 GSLC 栈，入口为
  `registered_stack.json`。

### 1.2 Repository Boundary / 仓库边界

本仓库负责数据查询与下载、DEM 准备、SLC 生成或导入、参考几何、RSLC 配准、
RSLC/GSLC 裁剪及预处理质量检查。基线选择、干涉图、相干性、滤波、解缠、双频
电离层改正、形变时序、MintPy 和 DS-InSAR 属于 `Geodesy-Analysis`。

通用下载功能不绑定处理软件；GAMMA 实现位于独立后端。以后增加 ISCE3 或自行开发的
方法时，应新增后端，不与 GAMMA 命令和参数混合。

## 2. Project Structure / 项目结构

```text
Geodesy-Preprocessing/
├── config_examples/                 # 按项目阶段分类的 CFG 模板
│   ├── acquisition/                 # SAR 查询和下载
│   ├── topography/                  # DEM 获取与转换
│   └── preprocessing/               # SLC、参考几何、配准与裁剪
├── docs/
│   ├── architecture.md              # 仓库边界与源码架构
│   ├── workflows/                   # 总路线及各产品完整流程
│   └── validation/                  # 当前有效的验收记录
├── src/geodesy_preprocessing/       # 可安装 Python 包
│   ├── common/                      # 日志、进度和公共运行协议
│   ├── dem/
│   │   ├── acquisition/             # 公共 DEM 获取
│   │   └── conversion/gamma/        # GAMMA DEM 转换
│   └── sar/
│       ├── acquisition/             # Sentinel-1 与 NISAR 查询/下载
│       ├── inventory/               # 日期和产品清单
│       ├── slc_generation/          # SLC 生成或导入
│       ├── rslc_registration/       # 参考几何和 RSLC 配准
│       ├── rslc_subsetting/         # 注册 RSLC 的研究区裁剪
│       └── gslc_subsetting/         # NISAR GSLC 校验与裁剪
├── tests/                           # 自动测试
└── pyproject.toml                   # 安装信息和正式命令入口
```

### 2.1 Documentation / 文档入口

- [预处理路线选择](docs/workflows/README.md)：根据输入产品选择处理路线。
- [DEM 流程](docs/workflows/dem.md)：公共 GeoTIFF 下载与可选 GAMMA 转换。
- [Sentinel-1 TOPS 流程](docs/workflows/sentinel1_tops.md)：查询、SLC、配准和裁剪。
- [radar-grid SAR 流程](docs/workflows/radar_grid.md)：多种非 TOPS SAR 的共用路线。
- [NISAR L1 RSLC 流程](docs/workflows/nisar_rslc.md)：LA/LB 独立 GAMMA 路线。
- [NISAR L2 GSLC 流程](docs/workflows/nisar_gslc.md)：Native 地图网格路线。
- [源码架构](docs/architecture.md)与[验收记录](docs/validation/preprocessing_acceptance.md)：
  供开发、维护和判断真实数据验证范围使用。

每份 `config_examples/<stage>/*.cfg` 只说明一个处理阶段的用途、调用方式、参数、输出、日志、
完成判断和重跑规则；流程文档负责把多个 CFG 串成完整路线。

## 3. Workflow Overview / 流程总览

### 3.1 Common DEM / 公共 DEM

```text
KML 或 bbox
  → OpenTopography GeoTIFF
  → 可选 GAMMA srtm2dem
  → 各 SAR 路线的参考几何
```

DEM 是研究区公共输入，不属于某颗卫星。同一 GeoTIFF 可以供多个轨道使用，但每个
雷达网格仍应建立自己的参考几何。

### 3.2 Sentinel-1 TOPS

```text
ASF 查询与覆盖图
  → BURST/SLC 下载（BURST 需要合并 SAFE）
  → GAMMA TOPS SLC
  → TOPS 参考几何与 RSLC 配准
  → KML 缓冲裁剪
  → registered_stack.json
```

GRD 可以查询和下载，但不进入当前 TOPS InSAR 复数影像路线。

### 3.3 Radar-grid SAR / 普通雷达坐标 SAR

```text
ALOS-2 / COSMO / LT-1 / RADARSAT-2 / SAOCOM 等任务产品
  → 对应 GAMMA SLC 适配器
  → 共用 radar-grid 参考几何与 RSLC 配准
  → KML 缓冲裁剪
  → registered_stack.json
```

卫星差异只保留在 SLC 适配阶段；生成普通 GAMMA 雷达坐标 SLC 后，共用配准和裁剪
实现。极化由 CFG 选择，可以只处理主极化，也可以发布完整多极化栈。

### 3.4 NISAR L1 RSLC

```text
Earthdata 查询、覆盖图与下载
  → par_NISAR_RSLC 导入 GAMMA SLC
  → radar-grid 参考几何与 RSLC 配准
  → KML 缓冲裁剪
```

Frequency A（LA）为默认主链；Frequency B（LB）具有独立网格，必须使用独立 case、
输出目录和参考几何。双频电离层计算不在预处理阶段执行。

### 3.5 NISAR L2 GSLC

```text
Earthdata 查询、覆盖图与下载
  → Native HDF5 网格一致性检查
  → complex64 KML 缓冲裁剪
  → geocoded-grid registered_stack.json
```

GSLC 已位于地图网格，不需要 GAMMA 配准，也不会被重新投影或重采样。LA 与 LB
分别校验和发布。

## 4. Dependencies And Installation / 环境与安装

### 4.1 Core Requirements / 核心依赖

- Python 3.10 或更高版本；服务器统一使用 `envInSAR`。
- GAMMA Software：仅 GAMMA DEM、SLC、参考几何、配准和 RSLC 裁剪路线需要。
- Earthdata 账号：Sentinel-1/NISAR 认证下载需要。
- OpenTopography API key：公共 DEM 下载需要。

Python 包依赖由 `pyproject.toml` 维护；覆盖图功能使用可选的 Matplotlib 依赖。

### 4.2 Installation / 安装

```bash
conda activate envInSAR
cd /path/to/Geodesy-Preprocessing
python -m pip install -e '.[plot]'
```

### 4.3 Environment Check / 环境检查

```bash
python -c "import geodesy_preprocessing, asf_search, h5py, rasterio, shapely; print('envInSAR OK')"
geodesy-pre-dem --help
geodesy-pre-sar-sentinel1-acquire --help
geodesy-pre-gamma-coreg-grid --help

# 只有使用 GAMMA 路线时检查：
command -v srtm2dem
command -v par_NISAR_RSLC
```

`par_NISAR_RSLC` 只用于 NISAR L1 RSLC；其他 GAMMA 路线不要求该命令。

## 5. Quick Start / 快速开始

### 5.1 Select A Workflow / 选择处理路线

先根据产品类型阅读[预处理路线选择](docs/workflows/README.md)。不要仅按卫星名称判断：
NISAR L1 RSLC 和 L2 GSLC 的数据几何不同，因此使用两条独立路线。

### 5.2 Copy And Edit CFG / 准备配置

CFG 属于具体研究区，应复制到项目目录后修改，不直接修改仓库模板：

```bash
PROJECT=/path/to/research_project
mkdir -p "$PROJECT/config/acquisition" "$PROJECT/config/topography" "$PROJECT/config/preprocessing" "$PROJECT/TOPOGRAPHY" "$PROJECT/S1/QUERY" "$PROJECT/S1/DOWNLOAD"
cp config_examples/topography/opentopo_dem_download.cfg "$PROJECT/config/topography/"
```

再复制所选路线需要的 CFG。模板顶部给出正式命令和示例，参数块标明哪些内容必填、
经常修改、通常不改或需要谨慎修改。

OpenTopography 模板默认在下载后执行 GAMMA `srtm2dem`；只需要公共 GeoTIFF 时将
`run_srtm2dem=false`。新项目只维护这一份数据集优先的 DEM 配置模板。

### 5.3 Run And Check Logs / 执行与检查日志

所有下载和批处理都应先检查计划，再正式运行。例如：

```bash
# DEM：先检查范围，再下载并按 CFG 决定是否转换为 GAMMA DEM
geodesy-pre-dem --config "$PROJECT/config/topography/opentopo_dem_download.cfg" --dry-run
geodesy-pre-dem --config "$PROJECT/config/topography/opentopo_dem_download.cfg"

# Sentinel-1：先查询和人工检查覆盖，再下载
geodesy-pre-sar-sentinel1-acquire \
  --config "$PROJECT/config/acquisition/sentinel1_download.cfg" --case main --query
geodesy-pre-sar-sentinel1-acquire \
  --config "$PROJECT/config/acquisition/sentinel1_download.cfg" --case main --download
```

完整 SLC 不需要 BURST 合并；BURST 下载后增加 `--merge`。SLC 生成、参考几何、配准和
裁剪的后续命令见对应流程文档。裁剪阶段应先执行 `--plan-only` 检查 KML 缓冲范围。

## 6. Project Data And Logs / 工程数据与日志

### 6.1 Recommended Project Layout / 推荐工程目录

```text
RESEARCH_PROJECT/
├── config/
│   ├── acquisition/                    # SAR 查询和下载 CFG
│   ├── topography/                     # DEM 获取与转换 CFG
│   └── preprocessing/                  # SLC、配准和裁剪 CFG
├── TOPOGRAPHY/
│   ├── catalog.json
│   └── <dataset_id>/{SOURCE,GAMMA}/    # 可被多个轨道复用的 DEM 数据集
└── S1/
    ├── QUERY/
    ├── DOWNLOAD/<CASE>/
    └── ASCENDING_128/
        ├── PRE/GAMMA/{SLC,GEOMETRY,RSLC,RSLC_SUBSET}/
        ├── ANALYSIS/GAMMA/              # 由 Geodesy-Analysis 管理
        ├── VISUALIZATION/GAMMA/         # 由绘图仓库管理
        └── LOG/PRE/GAMMA/
```

代码仓库只保存通用实现和空白模板。研究区 KML、填写后的 CFG、下载产品、中间结果和
日志全部留在研究项目中。

### 6.2 Configuration And Credentials / 配置与凭据

- CFG 中的相对路径以 CFG 文件所在目录为基准。
- Earthdata 凭据读取 `~/.netrc`、`EARTHDATA_TOKEN`，或成对的
  `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD`。
- OpenTopography 密钥读取 `OPENTOPO_API_KEY`。
- 账号、密码和 API key 不写入 CFG，也不提交到 Git。

### 6.3 Completion And Safe Reruns / 完成判断与安全重跑

终端只显示统一的逐景进度；完整 stdout/stderr 和失败信息写入项目 `LOG/PRE/` 或阶段
输出中的 `logs/`。完整且输入摘要一致的结果可以安全跳过；残缺结果或输入来源变化时
会停止，不会静默覆盖正式结果。

预处理完成不能只看命令退出码。最终应检查 `registered_stack.json`、日期清单、所选
极化、裁剪窗口和质量记录是否完整，再将栈清单交给 `Geodesy-Analysis`。

Git 保存代码和文档如何变化；项目 LOG 保存某个研究区的一次实际处理。两者不能互相
替代。

## 7. Verification And Development / 验证与开发

```bash
conda activate envInSAR
python -m unittest discover -s tests -q
git diff --check
```

自动测试验证配置、命令计划、安装入口、文件结构、日志、失败保护和安全重跑。真实数据
数值验收范围见[预处理验收记录](docs/validation/preprocessing_acceptance.md)。新增或修改
代码前还应阅读 [AGENTS.md](AGENTS.md)。

## 8. Security / 安全说明

不得向 CFG、README、测试夹具或 Git 历史提交真实凭据和受限制项目数据。建议长期凭据
保存在权限为 `600` 的 `~/.netrc`；临时令牌仅设置在当前终端环境。若密钥泄露，应立即
吊销并更换，同时清理仓库历史。
