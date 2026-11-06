<#
.SYNOPSIS
管理穹图新数据库，不修改归档版本的 battle_sandbox。
.PARAMETER Action
Setup 建立全部二十张业务表，Status 查询脱敏状态，Verify 验证隔离测试库，Backup 备份新库。
.PARAMETER EnableVectors
兼容旧入口的参数；当前 Setup 默认包含向量表。使用已准备的 pgvector，不自动下载扩展。
.NOTES
使用现有 Python 标准库与 PostgreSQL 命令行工具，凭据只保存在已忽略的私有目录。
#>
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Status', 'Verify', 'Backup')]
    [string]$Action = 'Status',
    [switch]$EnableVectors
)
$ErrorActionPreference = 'Stop'
$databaseArguments = @((Join-Path $PSScriptRoot 'database.py'), $Action.ToLowerInvariant())
if ($EnableVectors) { $databaseArguments += '--enable-vectors' }
& python @databaseArguments
if ($LASTEXITCODE -ne 0) { throw '数据库操作失败，请查看以上脱敏错误。' }
