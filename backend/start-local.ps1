param(
    [int]$Port = 18080,
    [switch]$Initialize,
    [switch]$NoScheduler
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "端口 $Port 已被占用，请检查现有服务或指定其他端口。"
    }
    if ($Initialize) {
        python -X utf8 -m backend.database setup
    } else {
        python -X utf8 -m backend.database status
    }
    if ($LASTEXITCODE -ne 0) { throw '数据库检查未通过，后端未启动。' }
    $serverArguments = @('-X', 'utf8', '-m', 'backend.main', '--port', "$Port")
    if ($Initialize) { $serverArguments += '--initialize' }
    if ($NoScheduler) { $serverArguments += '--no-scheduler' }
    python @serverArguments
    if ($LASTEXITCODE -ne 0) { throw '后端进程异常结束，请检查本机配置和日志。' }
} finally {
    Pop-Location
}
