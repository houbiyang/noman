# 青岚河谷战损视觉资产

2026-11-01。使用本机 Blender 5.2.1 LTS 原生 Python API 制作，模型和纹理由工程脚本生成，没有下载外部素材或安装扩展。

## 可编辑文件与运行资源

- `战损建筑源模型.blend`：四档住宅的可编辑源模型，包含打包的纹理。
- `战损河谷完整场景.blend`：从当前地图 JSON 与同源住宅组装的完整 Blender 场景，包含摄影机、日光、材质和打包纹理。
- `战损河谷_Blender渲染.png`：1440 × 960 的 Cycles 离线渲染。它不是网页运行截图，离线间接光和降噪效果与网页实时渲染不同。
- `战损河谷_Blender总览.png`：同一地图的离线总览；`render-manifest.json` 记录渲染对应的地图版本与内容哈希。
- `战损地图_网页总览.jpg`：最终 Unity Web 构建在本机浏览器中的实际总览截图。
- `asset-manifest.json`：模型三角形数量、纹理规格及发布资产的 SHA-256 校验值。
- Unity 原生运行资源位于 `Assets/Sandbox/Resources/Battlefield/`；四个 FBX 模型由 Unity 自带导入器读取，24 张 512 × 512 PNG 分别提供十二种底色与法线表面。

地图共布置 80 栋住宅：完整 19 栋、轻损 23 栋、重损 20 栋、坍塌 18 栋。模型包含实际门窗洞口、窗框、楼板、独立屋面片、椽木、烟囱和不规则瓦砾；重损模型保留破碎墙段和外露砖块。完整建筑也有老化灰墙，避免整片村落只由废墟构成。

四档模型分别为 3,176、4,436、4,822、5,126 个三角形。网页重复使用这四个网格和共享材质，不为每栋住宅复制一套 FBX。地形与道路图单独保存，损坏程度只影响视觉，没有实现摧毁、遮挡裁决或动态坍塌。

## 重新生成

从 `unity-client` 目录运行以下 PowerShell 命令。先关闭当前工程的 Unity 编辑器，生成完资源后再构建网页。

```powershell
# 生成原创材质、四种住宅 FBX 和建筑源文件
& 'D:\Blender\Blender 5.2\blender.exe' --background --factory-startup --python '.\Tools\build_battlefield_assets.py'

# 生成地表、道路及八十栋建筑的同源坐标
python '.\Tools\build_qinglan_map.py'

# 保存完整 Blender 场景并渲染地图近景
& 'D:\Blender\Blender 5.2\blender.exe' --background --factory-startup --python '.\Tools\build_battlefield_assets.py' -- --render-map

# 由 Unity 原生导入、保存场景并重新构建网页
& '.\start-unity.ps1' -Action BuildWeb
```

地形采用连续法线、米制 UV 和可平铺表面；河岸使用缓坡过渡。主光阴影范围扩大至 650 场景单位，使总览相机能够看到建筑和树木的落影。云层仍存在，改为更高、更薄的低透明度灰云。原鼠标相机、单位、角色、任务及存档逻辑保持原有职责。

当前视觉属于程序化写实方向，未使用摄影测量、卫星影像或真实地理高程；树木和部分保障建筑仍为简化网格。网页最终运行验证与截图见 `Verification/网页验证记录_2026-11-01.md`。
