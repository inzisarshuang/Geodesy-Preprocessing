# 预处理验收记录

更新时间：2026-10-06。本文只记录仍然有效的验收结论；代码迁移过程由 Git 历史保存。

## 已通过

- 2026-10-06 在 `envInSAR` 环境完成最终自动验收：453 项测试全部通过；测试覆盖源码
  架构、13 份 CFG、安装后的命令入口、DEM、Sentinel-1、普通 radar-grid SAR、
  NISAR RSLC/GSLC、裁剪、日志与失败保护。旧 GAMMA-InSAR 工具箱的 11 个兼容入口
  场景也已通过专项测试。
- 公共 DEM 下载与 GAMMA `srtm2dem` 转换：配置、边界、失败保护、参数文件和字节数
  校验均有自动测试。
- Sentinel-1 TOPS 的 SLC、参考几何和配准曾使用真实数据运行；配置、命令计划、只读
  输入、原子发布和逐景进度有自动测试。
- Sentinel-1 正式获取入口的 BURST/SLC/GRD 规范化、覆盖计算、查询指纹、安全下载、
  文件结构校验、凭据脱敏和 BURST→SAFE 有自动测试；真实 ASF BURST 查询已确认
  `SLC-BURST` 数据集与顶层 `groupID`。ASF 标称大小与有效文件实际大小存在轻微差异时，
  两个数值都会记录而不会误删有效产品。
- GAMMA 注册 RSLC 的 KML 缓冲和裁剪已通过自动测试。Sierra Oeste 2026/154 的真实
  `--plan-only` 通过；抽查 20260902 与 20260908 的旧裁剪成果，参数尺寸和复数文件
  字节数一致。新的 5 km KML 窗口与旧人工大窗口不同属于预期差异。
- 普通 radar-grid 参考几何和配准通过配置、命令参数、中心频率、多极化几何复用、
  质量门槛、失败日志、原子发布和安全重跑自动测试。GAMMA 2025 的 offset 窗口、
  估计点数、过采样、相关阈值和多项式参数已开放到 CFG 并进入实际命令。
- NISAR L1 RSLC 的查询、覆盖图、下载、HDF5 校验、`par_NISAR_RSLC` 导入、LA/LB
  独立路径和后续 radar-grid 接口通过自动测试。
- NISAR L2 GSLC 的 Native 查询、下载、网格一致性、KML 缓冲、complex64 裁剪和
  geocoded-grid 清单通过自动测试。Sierra Oeste T087/F022 的 2026-08-31 与
  2026-09-12 两景 LA/HH 已完成真实下载、5 km 缓冲裁剪、复数抽查、输入只读和安全
  重跑验收；两景为 EPSG:32630。

## 仍需真实数据数值验收

- 使用正式 Sentinel-1 新入口完成一次认证下载、BURST→SAFE，并从新入口完整生成一套
  裁剪后的 RSLC 栈。现有真实 TOPS 处理经验不能代替这一条新入口的端到端验收。
- 使用至少一景参考和一景同轨非参考的普通 radar-grid 数据，检查实际 GAMMA 配准质量、
  RSLC/RMLI 尺寸和 1–2 景裁剪结果。
- 下载真实 NISAR L1 RSLC，记录产品摘要、GAMMA 版本、两日期 LA 配准质量和裁剪抽查；
  若需要 LB，再单独执行并验收。
- NISAR GSLC 的 LB/HV 已确认在真实 HDF5 中存在，但尚未生成大体量派生栈。这不影响
  已完成的 LA/HH 主路线验收。

## 不属于本仓库

基线选择、干涉图、相干性、滤波、解缠、形变时序、MintPy、DS-InSAR 和双频电离层
计算由 `Geodesy-Analysis` 负责。预处理验收到裁剪后的 RSLC/GSLC
`registered_stack.json` 为止。
