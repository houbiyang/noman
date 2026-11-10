<#
.SYNOPSIS
以隐藏输入为当前 PowerShell 会话设置官方 Kimi、DeepSeek 密钥。
.PARAMETER Provider
选择 Kimi、DeepSeek 或 Both；仅设置当前进程环境，不写入文件或用户注册表。
.NOTES
请在本机终端运行；密钥不进入命令历史。随后须从同一终端重新启动后端。
#>
[CmdletBinding()]
param(
    [ValidateSet('Kimi', 'DeepSeek', 'Both')]
    [string]$Provider = 'Both'
)

$ErrorActionPreference = 'Stop'

function Set-SessionModelKey {
    <#
    .SYNOPSIS
    将隐藏输入写入当前进程的指定密钥变量，释放临时非托管缓冲区。
    .PARAMETER VariableName
    已明确约定的密钥环境变量名称。
    .PARAMETER ProviderName
    用于本机输入提示的服务名称。
    #>
    param([string]$VariableName, [string]$ProviderName)

    $secureValue = Read-Host "$ProviderName 官方 API 密钥（隐藏输入）" -AsSecureString
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        $secretValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
        if ([string]::IsNullOrWhiteSpace($secretValue)) { throw "$ProviderName 密钥不能为空，未修改环境变量。" }
        [Environment]::SetEnvironmentVariable($VariableName, $secretValue, 'Process')
        Write-Output "已设置当前 PowerShell 会话变量：$VariableName"
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
        $secretValue = $null
        $secureValue.Dispose()
    }
}

if ($Provider -in @('Kimi', 'Both')) { Set-SessionModelKey -VariableName 'MOONSHOT_API_KEY' -ProviderName 'Kimi' }
if ($Provider -in @('DeepSeek', 'Both')) { Set-SessionModelKey -VariableName 'DEEPSEEK_API_KEY' -ProviderName 'DeepSeek' }
Write-Output '密钥只在当前终端及其新启动的子进程中有效。已有后端不会自动获得这些变量；请按交付手册从同一终端重启。'
