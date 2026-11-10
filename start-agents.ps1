<#
.SYNOPSIS
检查并启动当前 Windows 电脑上的多智能体工作台。
.PARAMETER CheckOnly
仅核对本机依赖、文件、数据库和模型配置，不启动服务。
.PARAMETER RequireModel
要求红蓝模型配置及当前进程的密钥环境变量都已就绪，不调用付费模型。
.PARAMETER NoBrowser
服务就绪后不自动打开浏览器。
.NOTES
复用现有数据库与网页启动器，不安装依赖、不停止占用端口的进程。
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$RequireModel,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) { throw '未找到已有 Python 3.11；本入口不会自动安装依赖。' }

Push-Location -LiteralPath $PSScriptRoot
try {
    $checkArguments = @('-X', 'utf8', '-m', 'backend.delivery_check', '--json')
    if ($RequireModel) { $checkArguments += '--require-model' }
    $checkOutput = & $pythonCommand.Source @checkArguments
    $checkExitCode = $LASTEXITCODE
    if (-not $checkOutput) { throw '交付检查未返回结果，请检查当前 Python 与本机配置。' }
    $report = ($checkOutput -join "`n") | ConvertFrom-Json
    foreach ($check in $report.checks) {
        $label = if ($check.passed) { '通过' } else { '失败' }
        Write-Output "$label : $($check.name)"
    }
    if (-not $report.models_configured) {
        Write-Output '红蓝模型配置尚未齐备，请在工作台填写接口、模型名和密钥环境变量名。'
    }
    if ($checkExitCode -ne 0) { throw '本机交付条件未满足，未启动或重启业务服务。详细检查：python -X utf8 -m backend.delivery_check --json' }
    if ($CheckOnly) { return }

    & (Join-Path $PSScriptRoot 'unity-client\start-web.ps1') -NoBrowser
    if ($LASTEXITCODE -ne 0) { throw '已有网页启动器未成功完成。' }
    $serviceOutput = & $pythonCommand.Source -X utf8 -m backend.delivery_check --services --json
    $serviceExitCode = $LASTEXITCODE
    if ($serviceExitCode -ne 0) {
        throw '后端或工作台未就绪，或后端仍加载旧代码。请核对现有服务后按交付手册重启；本入口不会自动终止进程。'
    }
    Write-Output '多智能体工作台：http://127.0.0.1:18766/agents'
    Write-Output '检查仅证明软件启动和配置条件；真实模型推理按验收手册执行。'
    if (-not $NoBrowser) { Start-Process -FilePath 'http://127.0.0.1:18766/agents' | Out-Null }
} finally {
    Pop-Location
}
