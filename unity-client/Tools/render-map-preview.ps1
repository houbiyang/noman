<#
.SYNOPSIS
使用 Windows 原生绘图将同源三角网格渲染为地图资源预览，不启动 Unity。
.NOTES
仅使用 PowerShell 与 System.Drawing，不安装依赖；输出不作为 Unity 运行截图。
#>
#requires -Version 7.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$rendererSource = @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;

/// <summary>只用于资源审阅的等轴三角网格绘制器，不包含游戏运行逻辑。</summary>
public sealed class QinglanMapPreview
{
    private sealed class Face
    {
        public PointF[] points;
        public float depth;
        public Color color;
        public int order;
    }
    private readonly List<Face> faces = new List<Face>();

    /// <summary>将模型坐标投影到固定预览画布。</summary>
    public static PointF Project(float positionX, float height, float positionZ)
        => new PointF(650 + (positionX - positionZ) / 3 * 6.9282f, 1000 - (positionX + positionZ) / 3 * 4 - height / 3 * 8);

    /// <summary>接收与 Unity 完全相同的网格分块并计算可见面。</summary>
    public void AddPart(float[] vertices, int[] triangles, float red, float green, float blue, int order)
    {
        for (int offset = 0; offset < triangles.Length; offset += 3)
        {
            int first = triangles[offset] * 3;
            int second = triangles[offset + 1] * 3;
            int third = triangles[offset + 2] * 3;
            float directionX = vertices[second] - vertices[first];
            float directionY = vertices[second + 1] - vertices[first + 1];
            float directionZ = vertices[second + 2] - vertices[first + 2];
            float crossX = vertices[third] - vertices[first];
            float crossY = vertices[third + 1] - vertices[first + 1];
            float crossZ = vertices[third + 2] - vertices[first + 2];
            float normalX = directionY * crossZ - directionZ * crossY;
            float normalY = directionZ * crossX - directionX * crossZ;
            float normalZ = directionX * crossY - directionY * crossX;
            float magnitude = (float)Math.Sqrt(normalX * normalX + normalY * normalY + normalZ * normalZ);
            if (magnitude < 0.0000001f || -normalX + normalY - normalZ <= 0) continue;
            float light = Math.Max(0.4f, Math.Min(1.2f, 0.64f + (-normalX * 0.45f + normalY * 0.8f - normalZ * 0.3f) / magnitude * 0.45f));
            Func<float, int> component = value => Math.Min(255, Math.Max(0, (int)(value * light * 255)));
            faces.Add(new Face
            {
                points = new[] { Project(vertices[first], vertices[first + 1], vertices[first + 2]), Project(vertices[second], vertices[second + 1], vertices[second + 2]), Project(vertices[third], vertices[third + 1], vertices[third + 2]) },
                depth = vertices[first] + vertices[first + 2] + vertices[second] + vertices[second + 2] + vertices[third] + vertices[third + 2] - (vertices[first + 1] + vertices[second + 1] + vertices[third + 1]) * 1.4f,
                order = order,
                color = Color.FromArgb(component(red), component(green), component(blue))
            });
        }
    }

    /// <summary>按深度绘制实际模型面，返回可继续标注的位图。</summary>
    public Bitmap Render()
    {
        var image = new Bitmap(1920, 1200);
        using (Graphics canvas = Graphics.FromImage(image))
        {
            canvas.SmoothingMode = SmoothingMode.None;
            canvas.Clear(Color.FromArgb(18, 31, 35));
            faces.Sort((first, second) => first.order == second.order ? second.depth.CompareTo(first.depth) : first.order.CompareTo(second.order));
            var brushes = new Dictionary<int, SolidBrush>();
            foreach (Face face in faces)
            {
                int key = face.color.ToArgb();
                if (!brushes.TryGetValue(key, out SolidBrush brush))
                {
                    brush = new SolidBrush(face.color);
                    brushes.Add(key, brush);
                }
                canvas.FillPolygon(brush, face.points);
            }
            foreach (SolidBrush brush in brushes.Values) brush.Dispose();
        }
        return image;
    }
}
'@
$drawingReferences = @(Get-ChildItem -LiteralPath (Join-Path $PSHOME 'ref') -Filter '*.dll' | ForEach-Object { $_.FullName })
$drawingReferences += [System.Drawing.Bitmap].Assembly.Location
$drawingReferences += @(Get-ChildItem -LiteralPath $PSHOME -Filter 'System.Private.Windows*.dll' | ForEach-Object { $_.FullName })
Add-Type -TypeDefinition $rendererSource -ReferencedAssemblies $drawingReferences
$clientRoot = Split-Path -Parent $PSScriptRoot
$resourceRoot = Join-Path $clientRoot 'Assets\Sandbox\Resources\Maps\QinglanValley'
$definition = Get-Content -LiteralPath (Join-Path $resourceRoot 'map-definition.json') -Raw | ConvertFrom-Json
$geometry = Get-Content -LiteralPath (Join-Path $resourceRoot 'map-geometry.json') -Raw | ConvertFrom-Json
$palette = @{}
foreach ($material in $definition.materials) { $palette[$material.id] = $material }
$renderer = [QinglanMapPreview]::new()
foreach ($part in $geometry.parts) {
    $color = $palette[$part.material]
    $drawOrder = if ($part.name -eq 'terrain_Base') { 0 } elseif ($part.layer -eq 'terrain') { 1 } elseif ($part.layer -eq 'water') { 2 } elseif ($part.layer -eq 'grid') { 3 } else { 4 }
    $renderer.AddPart([float[]]$part.vertices, [int[]]$part.triangles, [float]$color.red, [float]$color.green, [float]$color.blue, $drawOrder)
}
$bitmap = $renderer.Render()
$graphics = [Drawing.Graphics]::FromImage($bitmap)
$graphics.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$titleFont = [Drawing.Font]::new('Microsoft YaHei', 29, [Drawing.FontStyle]::Bold)
$bodyFont = [Drawing.Font]::new('Microsoft YaHei', 13)
$smallFont = [Drawing.Font]::new('Microsoft YaHei', 10)
$white = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(233,239,222))
$muted = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(157,181,168))
$panel = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(29,48,48))
$line = [Drawing.Pen]::new([Drawing.Color]::FromArgb(160,196,180),1)
$graphics.DrawString('青岚河谷  /  QINGLAN VALLEY', $titleFont, $white, 75, 50)
$graphics.DrawString('虚构乡村河谷 · 360 × 240 场景单位 · 面积扩大九倍', $bodyFont, $muted, 78, 112)
$graphics.FillRectangle($panel, 1520, 180, 325, 720)
$legend = @('地图组成', '', '红蓝部署区 × 2', '兵营与无人机停靠点', '连通道路 × 31 段', '跨河桥梁 × 3', '条垄农田 × 24', '新增村镇住宅 × 36', '仓储区与物资交接位置', '河岸、沟渠与池塘', '林地、灌木与草地', '', '布局说明', '平地、山脊和河谷形成层次', '支路接入村镇与保障区域', '动态云层请在 Unity 中查看', '', '显示高度不参与规则结算')
for ($index = 0; $index -lt $legend.Count; $index++) {
    $graphics.DrawString($legend[$index], $bodyFont, $(if ($index -in @(0,12)) { $white } else { $muted }), 1543, (202 + $index * 34))
}
foreach ($marker in $definition.points) {
    $position = [QinglanMapPreview]::Project([float]$marker.x, 0.8, [float]$marker.z)
    $labelX = $position.X + $(if ($marker.x -lt 60) { -112 } else { 18 })
    $labelY = $position.Y + $(if ($marker.kind -eq 'camp') { 22 } elseif ($marker.kind -eq 'supply') { -34 } else { -18 })
    if ($marker.kind -eq 'camp') { $labelY = $position.Y - 38 }
    if ($marker.kind -eq 'evacuation' -and $marker.x -lt 60) { $labelX = $position.X + 10; $labelY = $position.Y + 14 }
    if ($marker.id -eq 'TASK_A') { $labelX = $position.X + 8; $labelY = $position.Y + 16 }
    if ($marker.id -eq 'BLUE_SUPPLY') { $labelY = $position.Y + 12 }
    $graphics.DrawLine($line, $position.X, $position.Y, $labelX + 10, $labelY + 12)
    $graphics.FillRectangle($panel, $labelX, $labelY, 138, 26)
    $graphics.DrawString($marker.name, $smallFont, $white, $labelX + 6, $labelY + 3)
}
$graphics.DrawString('模型资源的软件渲染预览 · 非 Unity 运行截图', $bodyFont, $white, 78, 1086)
$graphics.DrawString('同源交付：Unity 网格 JSON / OBJ + MTL / 版本化地图定义', $smallFont, $muted, 78, 1122)
$graphics.DrawString('地图资源 ID：' + $definition.mapId, $smallFont, $muted, 1270, 1122)
$outputPath = Join-Path $clientRoot 'MapPreview\青岚河谷_模型预览.png'
$bitmap.Save($outputPath, [Drawing.Imaging.ImageFormat]::Png)
foreach ($resource in @($graphics,$bitmap,$titleFont,$bodyFont,$smallFont,$white,$muted,$panel,$line)) { $resource.Dispose() }
Write-Output "地图模型预览已生成：$outputPath"
