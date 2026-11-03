# 十二公里策略地图

本版为原创 Blender 米制场景，可操作范围 12,000 × 12,000 米，面积 144 平方公里。外围地形只作背景；旧页面原型不参与地图构建。

城市、工业货场、机场、三处村镇与森林、农业地区采用非对称布局。主河宽约 300—460 米，新增西北、东南两条支流；三处主桥分别采用 24 米、32 米、24 米宽的桥面。对战区域增加西侧山地、东侧丘陵及南部洼地。支路连接两处加油站、两处带游乐设施的公园及两处农场。设施为静态显示模型，尚不提供加油、进驻、补给或摧毁业务行为。

建筑与植被使用共享模型，Unity 版本按空间块和材质合批。准确的建筑数、道路数、三角形数和同源哈希以本目录 `map-build-summary.json` 为准；场景与 JSON 由同一生成器输出。保存的 Blender 文件可直接编辑，Unity 网页需要重新构建才能更新。

## 重建

在 `unity-client` 目录执行：

```powershell
& 'D:\Blender\Blender 5.2\blender.exe' --background --factory-startup --python '.\Tools\build_strategic_map.py'
& .\Verification\check-map.ps1
& .\verify-source.ps1
& .\start-unity.ps1 -Action BuildWeb
& .\start-web.ps1 -NoBrowser
node .\Verification\check-web-build.cjs
```

`--no-render` 仅跳过离线图片；`--render-existing` 从本目录已有 Blender 场景渲染，不改地图 JSON。Blender 图片不作为网页画面证明。

## 参考与资产来源

- 设施类型参考《断箭》官方地图日志：https://www.matrixgames.com/news/broken-arrow-dev-diary-11-upcoming-beta-maps-and-tools
- 查询过 Unity Asset Store 的 Countryside gas station：https://assetstore.unity.com/packages/3d/environments/countryside-gas-station-132485 。该资源标注免费、Standard Unity Asset Store EULA、约 484 MB；本次没有下载或导入它。
- 本次新增场景、加油站、游乐设施、农场和建筑均由工程内 Blender 脚本原创生成，继续使用既有纹理，不增加第三方依赖。

当前是虚构策略地图的显示层：没有实际 GIS、权威寻路或水陆通行裁决。旧存档坐标通过显示投影兼容，未改写仿真状态。
