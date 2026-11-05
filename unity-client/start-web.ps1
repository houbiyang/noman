<#
.SYNOPSIS
启动 Unity 网页构建的本机 HTTP 服务，并在浏览器打开三维沙盘。
.PARAMETER Build
启动前重新构建网页；需要已激活的 Unity，且工程编辑器应先关闭。
.PARAMETER Port
本机监听端口，默认 18766；不修改防火墙或对外开放。
.PARAMETER NoBrowser
仅启动或核对服务，不自动打开浏览器。
#>
param(
    [switch]$Build,
    [ValidateRange(1024, 65535)]
    [int]$Port = 18766,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { throw '未找到本机已有的 Python 3；脚本不会自动安装依赖。' }
$logDirectory = Join-Path $projectRoot '.local\unity'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Test-BackendReady {
    # 只探测本项目固定的本机端口，不在页面传递数据库或引导凭据。
    try { return (Invoke-RestMethod -Uri 'http://127.0.0.1:18080/api/v1/health' -TimeoutSec 2).database_ready -eq $true }
    catch { return $false }
}

if (-not (Test-BackendReady)) {
    Push-Location -LiteralPath $projectRoot
    try {
        & $python.Source -X utf8 -m backend.database status | Out-Null
        if ($LASTEXITCODE -ne 0) { throw '已有项目数据库未就绪，未创建或重置数据库。' }
    } finally { Pop-Location }
    if (-not (Test-BackendReady)) {
        if (Get-NetTCPConnection -LocalPort 18080 -State Listen -ErrorAction SilentlyContinue) { throw '本机后端端口已占用但服务未就绪，请查看后端日志。' }
        $backend = Start-Process -FilePath $python.Source -ArgumentList @('-X','utf8','-m','backend.main','--port','18080','--initialize') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'backend-auto.out.log') -RedirectStandardError (Join-Path $logDirectory 'backend-auto.err.log')
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if (Test-BackendReady) { break }
            if ($backend.HasExited) { break }
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not (Test-BackendReady)) { throw '本机后端未能就绪，请查看 .local\unity\backend-auto.err.log。' }
}

if ($Build) {
    & (Join-Path $PSScriptRoot 'start-unity.ps1') -Action BuildWeb
}
$webRoot = Join-Path $PSScriptRoot 'Builds\Web'
if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'index.html'))) {
    throw '尚未生成 Unity 网页版。请关闭项目编辑器后执行 .\start-web.ps1 -Build。'
}
$webRoot = (Resolve-Path -LiteralPath $webRoot).Path
$sha256 = [Security.Cryptography.SHA256]::Create()
try { $projectId = ([BitConverter]::ToString($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($webRoot)))).Replace('-', '').ToLowerInvariant() }
finally { $sha256.Dispose() }
$url = "http://127.0.0.1:$Port/"

function Get-WebServiceId {
    # 仅探测本机明确指定的端口；不存在的服务属于正常启动分支。
    try { return (Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2).Headers['X-Qiongtu-Web'] }
    catch { return $null }
}

$existingId = Get-WebServiceId
if ($existingId -and $existingId -ne $projectId) { throw "端口 $Port 已被其他服务占用，请使用 -Port 指定其他端口。" }
if ($existingId -ne $projectId) {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) { throw '未找到本机已有的 Python 3；脚本不会自动安装依赖。' }
    $logDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) '.local\unity'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $serverScript = Join-Path $PSScriptRoot 'Tools\serve_web.py'
    $server = Start-Process -FilePath $python.Source -ArgumentList @('-u', ('"' + $serverScript + '"'), '--directory', ('"' + $webRoot + '"'), '--port', $Port) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory "web-$Port.out.log") -RedirectStandardError (Join-Path $logDirectory "web-$Port.err.log")
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ((Get-WebServiceId) -eq $projectId) { $ready = $true; break }
        if ($server.HasExited) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw "网页服务未能就绪，请查看 .local\unity\web-$Port.err.log；端口可能已被占用。" }
    Set-Content -LiteralPath (Join-Path $logDirectory "web-$Port.pid") -Value $server.Id -Encoding ascii
}
Write-Output "穹图 Unity 网页版：$url"
if (-not $NoBrowser) { Start-Process -FilePath $url | Out-Null }
