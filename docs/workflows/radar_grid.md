# radar-grid SAR 流程

这条路线适用于 ALOS-2、COSMO-SkyMed、LT-1、RADARSAT-2、SAOCOM，以及已经导入
为普通 GAMMA 雷达坐标 SLC 的其他任务产品。卫星差异停留在 SLC 适配器；SLC 之后
共用 GAMMA 参考几何、RSLC 配准和裁剪实现。

## 生成 SLC

在 `gamma_slc_grid.cfg` 中选择传感器适配器、原始产品目录、极化和输出位置：

```bash
geodesy-pre-gamma-slc-grid --config PROJECT/config/preprocessing/gamma_slc_grid.cfg --case main --dry-run
geodesy-pre-gamma-slc-grid --config PROJECT/config/preprocessing/gamma_slc_grid.cfg --case main
```

完成时 `slc_dir` 中应有每个日期/极化的复数 SLC 与参数文件，`images_file` 日期唯一且
排序。缺少某个日期或极化时不要继续建立不完整栈。

## 建立参考几何

按[DEM 流程](dem.md)准备 GAMMA DEM，在 `gamma_geo_ref_grid.cfg` 中选择参考日期和主极化：

```bash
geodesy-pre-gamma-geo-ref-grid --config PROJECT/config/preprocessing/gamma_geo_ref_grid.cfg --case main --dry-run
geodesy-pre-gamma-geo-ref-grid --config PROJECT/config/preprocessing/gamma_geo_ref_grid.cfg --case main
```

完成标志是 `reference_geometry.json` 以及对应 lookup、雷达坐标高程和质量文件完整。

## 配准为 RSLC

在 `gamma_coreg_grid.cfg` 中设置极化、参考日期和输出目录。通常保留 offset 参数默认值；
分辨率明显变化或弱相关时，再结合质量日志调整窗口、估计点数和相关阈值。

```bash
geodesy-pre-gamma-coreg-grid --config PROJECT/config/preprocessing/gamma_coreg_grid.cfg --case main --dry-run
geodesy-pre-gamma-coreg-grid --config PROJECT/config/preprocessing/gamma_coreg_grid.cfg --case main
```

完成时 `registered_stack.json`、`quality.json` 和 `provenance.json` 必须可读，且日期×极化
网格完整。

默认 `quality_policy = strict`：任何景达到硬失败阈值时不发布整个栈。科研中需要保留
失败结果制作干涉图或比较算法时，可另建 case，设置 `quality_policy = diagnostic`，并把
`output_dir` 指向独立的 `RSLC_DIAGNOSTIC`。诊断模式会继续处理剩余极化并发布完整栈，
但 `quality.json` 仍明确记录 `stack_quality_status = failed`，不会掩盖质量问题。

## 裁剪和交接

```bash
geodesy-pre-gamma-rslc-crop --config PROJECT/config/preprocessing/gamma_rslc_crop.cfg --case main --plan-only
geodesy-pre-gamma-rslc-crop --config PROJECT/config/preprocessing/gamma_rslc_crop.cfg --case main
```

先确认 KML 缓冲范围没有被缩减且 lookup 覆盖足够，再正式发布。最终
`registered_stack.json` 是交给 `Geodesy-Analysis` 的入口。日志位于项目 `LOG/PRE/`
和各阶段输出内部的逐景 `logs/`。
