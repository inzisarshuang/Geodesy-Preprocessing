# DEM 流程

公共 DEM 不属于某颗 SAR 卫星。同一研究区的 GeoTIFF 可以供多个轨道和后端使用；
`geodesy-pre-dem` 可以在下载后按配置继续生成 GAMMA DEM。不同轨道仍应各自建立
雷达参考几何。

## 准备配置

只需将 `config_examples/opentopo_dem_download.cfg` 复制到项目的
`config/preprocessing/`。在同一 CFG 中设置项目根目录、唯一
`topography_dataset_id`、KML 或 bbox 和缓冲距离。输出位置由数据集 ID 确定，API key
放在运行终端的 `OPENTOPO_API_KEY`，不要放进 CFG。

## 下载并按需转换

```bash
conda activate envInSAR
geodesy-pre-dem --config PROJECT/config/preprocessing/opentopo_dem_download.cfg --dry-run
geodesy-pre-dem --config PROJECT/config/preprocessing/opentopo_dem_download.cfg
```

模板默认 `run_srtm2dem=true`，因此完成时会得到：

```text
TOPOGRAPHY/
├── catalog.json
└── <dataset_id>/
    ├── SOURCE/
    │   ├── <dataset_id>.tif
    │   └── download_manifest.json
    ├── GAMMA/
    │   ├── <dataset_id>.dem
    │   ├── <dataset_id>.dem.par
    │   ├── conversion.log
    │   └── conversion_manifest.json
    └── topography_manifest.json
```

只需要公共 GeoTIFF 时设置
`run_srtm2dem=false`。参数文件中的宽高与
GAMMA DEM 字节数必须一致。垂向基准由输入 DEM 和 `geoid_correction_flag` 决定，命令
成功不等于基准设置一定正确。

重复运行时，已有 GeoTIFF 会在校验后复用，不再读取 API key 或联网；完整 GAMMA
结果也会校验后跳过。残缺 GAMMA 结果会停止并要求人工检查。旧项目原有的独立转换
CFG 仍可由兼容命令读取，但新项目不再维护第二份模板。

## 日志与下一阶段

直接运行看终端和数据集 `GAMMA/conversion.log`；Workflow 的 case 运行记录位于
`<case>/LOG/PRE/GAMMA/`。之后让一个或多个 SAR case 的参考几何引用该数据集。
