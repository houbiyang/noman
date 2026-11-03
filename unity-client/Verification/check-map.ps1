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
Assert-MapCheck ($map.nodes.Count -ge 80 -and $map.roads.Count -ge 120) '十二公里策略地图具有完整道路拓扑'
Assert-MapCheck ($map.width -eq 12000 -and $map.length -eq 12000 -and $map.displayScale -eq 1 -and $map.coordinateUnit -eq 'meter') '地图为一万二千米米制范围'
Assert-MapCheck (@($map.roads | Where-Object isBridge).Count -eq 3) '北中南三处跨河桥接'
Assert-MapCheck ($map.points.Count -eq 11) '十一处部署与任务标记身份完整'
Assert-MapCheck ($geometry.parts.Count -ge 300) '地形、城市、机场、货场与植被按空间块导出'
Assert-MapCheck ($map.buildings.Count -ge 900 -and $map.buildingsBaked) '建筑由 Blender 同源几何合批入图'
Assert-MapCheck (@($map.buildings.model | Sort-Object -Unique).Count -ge 12) '住宅、公寓、工业、机场和保障模型均已布置'
Assert-MapCheck (@($map.buildings | Where-Object model -like 'Strategic_House_*').Count -ge 300) '村镇包含可辨认的原创住宅层级'
foreach ($surface in @('Asphalt','Paving','Gravel','Grass')) {
    Assert-MapCheck (@($geometry.parts | Where-Object material -eq $surface).Count -gt 0) ('桥头道路和地表包含：'+$surface)
}
foreach ($layer in @('terrain','water','routes','facilities','farmland','village','logistics','environment','grid','settlement')) {
    Assert-MapCheck (@($geometry.parts | Where-Object layer -eq $layer).Count -gt 0) ('包含环境图层：' + $layer)
}
Assert-MapCheck (($map.SampleHeight(1000,1000) -ne $map.SampleHeight(11000,11000)) -and ($map.SampleHeight(1000,1000) -lt 300)) '外围丘陵与河谷地形非镜像且高程有限'
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
