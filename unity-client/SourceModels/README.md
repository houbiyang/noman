# 军队模型源文件

`sandbox-units.blend` 是本次需求提供的 Blender 源文件副本。三套模型分别为 `recon`（四旋翼侦察机）、`infantry`（履带地面单元）、`support`（六轮保障车）。源文件放在 Assets 外，避免 Unity 自动触发 .blend 转换；运行时使用经过导出的 FBX 和 Unity 原生预制体。

在仓库根目录执行：

```sh
/Applications/Blender.app/Contents/MacOS/Blender \
  --background unity-client/SourceModels/sandbox-units.blend \
  --python unity-client/Tools/export_army_models.py -- \
  "$PWD/unity-client/Assets/Sandbox/Units/Source"

unity-client/start-unity.command Prepare
unity-client/start-unity.command BuildWeb
```

Unity 菜单「穹图 / 军队 / 导入模型与渲染卡牌」也可重新生成资源。工具从源文件导出精确的零件材质对应关系，按材质合并静态网格，保留四个旋翼转轴、八个履带轮轴和六组轮胎轮毂转轴；生成 URP Lit 材质、可实例化的预制体和 780×600 的真实 Unity 模型卡图。生成结果位于 `Assets/Sandbox/Resources/Army`，不依赖本机 Blender 即可运行。

点击地图右侧「军队」打开军队配置；卡片数量代表本次新增数量（1–10，受剩余名额限制）。点击卡片「选点追加部署」，再单击地图追加同类模型；也可点击「集结区部署」新增三个卡片所选数量。所有部署保留已有模型及其位置和移动目标。选择单位后，可在单位面板点击「地图移动」，或按 Shift 单击地图设置目标；F 跟随，Esc 取消。地面单位使用本地图水面、建筑和桥梁生成的 A* 栅格路径，侦察机直接飞往目标。移动具有加速、转向、旋翼、轮转与轻微悬挂动画。

军队数量、单位身份和目标坐标纳入原有本机快照；旧版六单位存档仍可恢复。红蓝双方地图总计最多 10 个单位，包含 6 个初始单位；超额快照拒绝恢复，不会自动删除原模型。界面显示已部署数量和剩余名额，主持人可为双方部署，但共用相同的 10 个名额。角色限制由本地演示适配器检查，尚未接入服务端身份认证或正式任务结算；地面寻路属于客户端地图表现，未实现单位之间的动态避碰。快照保存的是目标坐标，恢复后单位出现在所保存的目标位置。
