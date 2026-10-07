# DEM 流程

公共 DEM 不属于某颗 SAR 卫星，也不属于 GAMMA。同一研究区的 GeoTIFF 可以供多个
轨道和后端使用；不同轨道仍应各自建立雷达参考几何。

## 准备配置

将 `config_examples/opentopo_dem_download.cfg` 和 `config_examples/gamma_dem_convert.cfg` 复制到
项目的 `config/preprocessing/`。在下载 CFG 中设置 KML 或 bbox、缓冲距离、DEM 数据集
与输出位置。API key 放在运行终端的 `OPENTOPO_API_KEY`，不要放进 CFG。

## 下载公共 GeoTIFF

```bash
conda activate envInSAR
geodesy-pre-dem --config PROJECT/config/preprocessing/opentopo_dem_download.cfg --dry-run
geodesy-pre-dem --config PROJECT/config/preprocessing/opentopo_dem_download.cfg
```

完成时应同时得到 GeoTIFF 和来源 JSON；默认示例位置是 `PROJECT/INPUT/DEM/dem.tif`。
下载失败不会发布半成品。

## 转换为 GAMMA DEM

只有 GAMMA 路线需要此阶段：

```bash
geodesy-pre-gamma-dem-convert --config PROJECT/config/preprocessing/gamma_dem_convert.cfg --dry-run
geodesy-pre-gamma-dem-convert --config PROJECT/config/preprocessing/gamma_dem_convert.cfg
```

完成时应得到 `srtm.dem`、`srtm.dem.par` 和 `srtm2dem.log`。参数文件中的宽高与
`srtm.dem` 字节数必须一致。垂向基准由输入 DEM 和 `geoid_correction_flag` 决定，命令
成功不等于基准设置一定正确。

## 日志与下一阶段

直接运行看终端和转换日志；Workflow 运行记录位于 `PROJECT/LOG/PRE/COMMON/DEM` 与
`PROJECT/LOG/PRE/GAMMA/DEM`。之后进入所选 SAR 路线的参考几何阶段。
