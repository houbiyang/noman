<#
.SYNOPSIS
对实际导出的青岚河谷布局和网格执行独立字段、非对称布局与负例检查。
.NOTES
仅使用 PowerShell 自带 C# 编译和 JSON 能力，不需要 Unity 许可证。
#>
#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$clientRoot = Split-Path -Parent $PSScriptRoot
Add-Type -Path (Join-Path $clientRoot 'Assets\Sandbox\Scripts\Domain\SandboxMapData.cs')
$options = [System.Text.Json.JsonSerializerOptions]::new()
$options.IncludeFields = $true
$resourceRoot = Join-Path $clientRoot 'Assets\Sandbox\Resources\Maps\QinglanValley'
$map = [System.Text.Json.JsonSerializer]::Deserialize([IO.File]::ReadAllText((Join-Path $resourceRoot 'map-definition.json')), [Qiongtu.Sandbox.SandboxMapData], $options)
$geometry = [System.Text.Json.JsonSerializer]::Deserialize([IO.File]::ReadAllText((Join-Path $resourceRoot 'map-geometry.json')), [Qiongtu.Sandbox.SandboxMapGeometryData], $options)
$script:checks = 0
function Assert-MapCheck {
    param([bool]$Condition, [string]$Description)
    if (-not $Condition) { throw "地图检查失败：$Description" }
    $script:checks++
}
Assert-MapCheck ($map.Validate() -eq '') '真实导出布局通过校验'
Assert-MapCheck ($geometry.Validate($map) -eq '') '真实导出网格与布局匹配'
Assert-MapCheck ($map.nodes.Count -eq 30 -and $map.roads.Count -eq 31) '扩展地图包含 30 节点与 31 条道路'
Assert-MapCheck ($map.width -eq 360 -and $map.length -eq 240 -and $map.displayScale -eq 3) '显示范围扩大为三百六十乘二百四十'
Assert-MapCheck (@($map.roads | Where-Object isBridge).Count -eq 3) '南中北有三处桥接'
Assert-MapCheck ($map.points.Count -eq 11) '十一处地图对象身份完整'
Assert-MapCheck ($geometry.parts.Count -eq 43) '战损河谷地表合并为四十三个网格分块'
Assert-MapCheck ($map.buildings.Count -eq 80) '八十栋 Blender 建筑使用同源显示布局'
Assert-MapCheck (@($map.buildings.damage | Sort-Object -Unique).Count -eq 4) '完整、轻损、重损和坍塌四档模型均已布置'
Assert-MapCheck (@($map.buildings | Where-Object layer -eq 'central_village').Count -gt 0) '中央作战区也有战损建筑'
foreach ($layer in @('riverbanks','farmland','ditches','village','logistics','ponds','scrub','central_village','landscape','damage')) {
    Assert-MapCheck (@($geometry.parts | Where-Object layer -eq $layer).Count -gt 0) ('包含环境图层：' + $layer)
}
$centralBridge = $map.points | Where-Object id -eq 'TASK_B'
Assert-MapCheck ([Math]::Abs($map.SampleSurfaceHeight($centralBridge.x,$centralBridge.z) - 3.75) -lt 0.001) '曲流中央桥面高度有效'
Assert-MapCheck ($map.SampleHeight($centralBridge.x,$centralBridge.z) -lt 0) '桥面与河床高度不混用'
Assert-MapCheck ([Math]::Abs($map.SampleHeight(57,216)-$map.SampleHeight(303,24)) -gt 0.1) '两岸地形不再中心对称'
$westExit=$map.nodes | Where-Object id -eq 'R_EXIT'
$eastExit=$map.nodes | Where-Object id -eq 'B_EXIT'
Assert-MapCheck ([Math]::Abs($westExit.x+$eastExit.x-360)+[Math]::Abs($westExit.z+$eastExit.z-240) -gt 0.1) '道路坐标不再镜像'
foreach ($marker in $map.points) {
    $height=$map.SampleSurfaceHeight($marker.x,$marker.z)
    Assert-MapCheck (-not [float]::IsNaN($height) -and -not [float]::IsInfinity($height)) ('地图标记具有有效地表：'+$marker.id)
}
$originalCoordinate=$map.nodes[0].x
$map.nodes[0].x+=0.5
Assert-MapCheck ($map.Validate() -eq '') '合法非对称节点坐标允许通过'
$map.nodes[0].x=$originalCoordinate
$originalEnd = $map.roads[0].endId
$map.roads[0].endId = 'UNKNOWN_NODE'
Assert-MapCheck ($map.Validate() -ne '') '无效道路引用被拒绝'
$map.roads[0].endId = $originalEnd
$originalBound = $map.deploymentZones[0].minX
$map.deploymentZones[0].minX = [float]::NaN
Assert-MapCheck ($map.Validate() -ne '') '非有限部署边界被拒绝'
$map.deploymentZones[0].minX = $originalBound
$originalIndex = $geometry.parts[0].triangles[0]
$geometry.parts[0].triangles[0] = [int]::MaxValue
Assert-MapCheck ($geometry.Validate($map) -ne '') '越界三角形索引被拒绝'
$geometry.parts[0].triangles[0] = $originalIndex
$originalHash = $geometry.contentHash
$geometry.contentHash = 'wrong-layout'
Assert-MapCheck ($geometry.Validate($map) -ne '') '混用地图版本被拒绝'
$geometry.contentHash = $originalHash
$originalModel = $map.buildings[0].model
$map.buildings[0].model = 'UNKNOWN_MODEL'
Assert-MapCheck ($map.Validate() -ne '') '未知战损模型被拒绝'
$map.buildings[0].model = $originalModel
$originalScale = $map.buildings[0].scaleY
$map.buildings[0].scaleY = [float]::NaN
Assert-MapCheck ($map.Validate() -ne '') '非有限建筑缩放被拒绝'
$map.buildings[0].scaleY = $originalScale
Assert-MapCheck ($map.Validate() -eq '' -and $geometry.Validate($map) -eq '') '负例检查未污染正确数据'
Write-Output "地图检查通过：$script:checks 项；静态连通与资源有效不代表正式寻路、任务或 Unity 画面验收。"
