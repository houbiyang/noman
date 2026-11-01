<#
.SYNOPSIS
使用本机已安装的 Unity 打开工程、生成场景或构建 Windows、网页客户端。
.PARAMETER Action
Open 打开编辑器；Run 运行沙盘；Prepare 生成入口场景；Build 构建 Windows；BuildWeb 构建网页。
.PARAMETER EditorPath
本机已安装的 Unity.exe 路径。
.NOTES
必须先在 Unity Hub 激活适用许可证；脚本不安装软件或处理账号凭据。
#>
param(
    [ValidateSet('Open', 'Run', 'Prepare', 'Build', 'BuildWeb')]
    [string]$Action = 'Open',
    [string]$EditorPath = 'D:\Unity\Editor\6000.6.7f1\Editor\Unity.exe'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $EditorPath)) { throw '未找到 Unity.exe，请通过 EditorPath 参数指定已安装的编辑器。' }
$projectPath = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$quotedProjectPath = '"' + $projectPath + '"'
if ($Action -eq 'Open') {
    Start-Process -FilePath $EditorPath -ArgumentList @('-projectPath', $quotedProjectPath) | Out-Null
    Write-Output '已请求打开 Unity 工程；编辑器就绪和运行结果需另外确认。'
    exit 0
}
$logDirectory = Join-Path (Split-Path -Parent $projectPath) '.local\unity'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logPath = Join-Path $logDirectory ($Action.ToLowerInvariant() + '.log')
if ($Action -eq 'Run') {
    $previewProcess = Start-Process -FilePath $EditorPath -ArgumentList @('-projectPath', $quotedProjectPath, '-executeMethod', 'Qiongtu.Sandbox.Editor.SandboxProjectSetup.RunPreview', '-logFile', ('"' + $logPath + '"')) -PassThru
    Write-Output "已请求在图形编辑器运行沙盘。进程：$($previewProcess.Id)，日志：$logPath"
    exit 0
}
$methodName = switch ($Action) {
    'Prepare' { 'PrepareScene' }
    'Build' { 'BuildWindows' }
    'BuildWeb' { 'BuildWeb' }
}
$arguments = @('-batchmode', '-quit', '-projectPath', $quotedProjectPath, '-executeMethod', ('Qiongtu.Sandbox.Editor.SandboxProjectSetup.' + $methodName), '-logFile', ('"' + $logPath + '"'))
if ($Action -eq 'BuildWeb') { $arguments += @('-buildTarget', 'WebGL') }
$process = Start-Process -FilePath $EditorPath -ArgumentList $arguments -WindowStyle Hidden -PassThru
$process.WaitForExit()
if ($process.ExitCode -eq 198) { throw 'Unity 未找到有效许可证。请先在 Unity Hub 激活许可证后重试。' }
if ($process.ExitCode -ne 0) { throw "Unity 执行失败，退出码 $($process.ExitCode)。日志：$logPath" }
Write-Output "Unity $Action 完成。日志：$logPath"
