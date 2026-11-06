<#
.SYNOPSIS
使用本机 Unity 自带编译器核对阶段一源码，不启动或激活编辑器。
.PARAMETER EditorDirectory
已安装 Unity 的 Editor 目录。
.PARAMETER DomainOnly
仅使用 PowerShell 自带编译能力执行纯状态检查，不需要 Unity SDK。
.NOTES
使用已有 SDK 和模板程序集缓存；不下载或安装依赖，不替代编辑器导入与运行验收。
#>
#requires -Version 7.0
param(
    [string]$EditorDirectory = 'D:\Unity\Editor\6000.6.7f1\Editor',
    [switch]$DomainOnly
)

$ErrorActionPreference = 'Stop'
$clientRoot = $PSScriptRoot
$markupPath = Join-Path $clientRoot 'Assets\Sandbox\Resources\Sandbox\SandboxHud.uxml'
$controllerPath = Join-Path $clientRoot 'Assets\Sandbox\Scripts\Presentation\SandboxHudController.cs'
[xml]$markup = Get-Content -LiteralPath $markupPath -Raw
$namedElements = @($markup.SelectNodes('//*[@name]') | ForEach-Object { $_.GetAttribute('name') })
if (@($namedElements | Group-Object | Where-Object { $_.Count -gt 1 }).Count -gt 0) { throw 'UXML 中存在重复的元素名称' }
$controllerContent = Get-Content -LiteralPath $controllerPath -Raw
$bindingPattern = '(?:BindButton|SetText)\("(?<name>[^"]+)"|root\.Q(?:<[^>]+>)?\("(?<name>[^"]+)"'
foreach ($binding in [regex]::Matches($controllerContent, $bindingPattern)) {
    $elementName = $binding.Groups['name'].Value
    if ($elementName -notin $namedElements) { throw "界面绑定没有对应元素：$elementName" }
}
Write-Output "UXML 结构与静态控件名称检查通过：$($namedElements.Count) 个具名元素；这不替代 Unity 资源导入。"
if ($DomainOnly) {
    $domainSources = @((Join-Path $clientRoot 'Assets\Sandbox\Scripts\Domain\SandboxSession.cs'), (Join-Path $clientRoot 'Assets\Sandbox\Scripts\Domain\SandboxArmy.cs'), (Join-Path $clientRoot 'Verification\DomainChecks.cs'))
    $compiledTypes = @(Add-Type -Path $domainSources -PassThru -ErrorAction Stop)
    $checkType = $compiledTypes | Where-Object { $_.Name -eq 'DomainChecks' }
    $entryPoint = $checkType.GetMethod('Main', [Reflection.BindingFlags]'NonPublic,Static')
    $result = $entryPoint.Invoke($null, $null)
    if ($result -ne 0) { throw '前端状态检查失败' }
    foreach ($sourceFile in @(Get-ChildItem -LiteralPath (Join-Path $clientRoot 'Assets\Sandbox') -Filter '*.cs' -Recurse)) {
        $syntaxTree = [Microsoft.CodeAnalysis.CSharp.CSharpSyntaxTree]::ParseText([IO.File]::ReadAllText($sourceFile.FullName))
        $syntaxErrors = @($syntaxTree.GetDiagnostics() | Where-Object { $_.Severity.ToString() -eq 'Error' })
        if ($syntaxErrors.Count -gt 0) { throw ("C# 语法检查失败：" + $sourceFile.Name + [Environment]::NewLine + ($syntaxErrors -join [Environment]::NewLine)) }
    }
    Write-Output '全部新增 C# 文件的语法解析通过；引擎 API 类型检查与运行结果须另外核对。'
    exit 0
}
$repositoryRoot = Split-Path -Parent $clientRoot
$outputDirectory = Join-Path $repositoryRoot '.local\unity\source-check'
$sdkRoot = Join-Path $EditorDirectory 'Data\DotNetSdk'
$dotnetPath = Join-Path $sdkRoot 'dotnet.exe'
if (-not (Test-Path -LiteralPath $dotnetPath)) { throw 'Unity 自带 SDK 不完整；请等待编辑器安装完成后重试。' }
$compilerPath = @(Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'sdk') -Filter csc.dll -Recurse | Sort-Object FullName -Descending)[0].FullName
$netStandardDirectory = @(Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'packs\NETStandard.Library.Ref') -Directory | Sort-Object Name -Descending)[0].FullName
$netStandardDirectory = Join-Path $netStandardDirectory 'ref\netstandard2.1'
$netRuntimeDirectory = @(Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'packs\Microsoft.NETCore.App.Ref') -Directory | Sort-Object Name -Descending)[0].FullName
$netRuntimeDirectory = Join-Path $netRuntimeDirectory 'ref\net8.0'
$unityManagedDirectory = Join-Path $EditorDirectory 'Data\Managed'
$unityModules = Join-Path $unityManagedDirectory 'UnityEngine'
$packageAssemblyDirectory = Join-Path $clientRoot 'Library\ScriptAssemblies'
foreach ($requiredDirectory in @($netStandardDirectory, $netRuntimeDirectory, $unityModules, $packageAssemblyDirectory)) {
    if (-not (Test-Path -LiteralPath $requiredDirectory)) { throw "缺少现有编译引用：$requiredDirectory。请在激活后先由 Unity 导入工程。" }
}
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
function Invoke-SourceCompiler {
    param([string[]]$CompilerArguments, [string]$ResponseName)
    # 使用响应文件避开 Windows 命令行长度限制，保留含中文和空格的完整路径。
    $responsePath = Join-Path $outputDirectory $ResponseName
    $quotedArguments = @($CompilerArguments | Where-Object { $_ -ne '-noconfig' } | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
    [IO.File]::WriteAllLines($responsePath, $quotedArguments, [Text.UTF8Encoding]::new($true))
    & $dotnetPath $compilerPath -noconfig ('@' + $responsePath)
}
$baseArguments = @('-nologo', '-noconfig', '-nostdlib+', '-langversion:9.0', '-warnaserror+', '-target:library')
$references = @(Get-ChildItem -LiteralPath $netStandardDirectory -Filter '*.dll' | ForEach-Object { '-reference:' + $_.FullName })
$references += @(Get-ChildItem -LiteralPath $unityModules -Filter '*.dll' | ForEach-Object { '-reference:' + $_.FullName })
$packageNames = @('Unity.InputSystem.dll', 'Unity.RenderPipelines.Core.Runtime.dll', 'Unity.RenderPipelines.Universal.Runtime.dll', 'Unity.RenderPipelines.Universal.Config.Runtime.dll', 'Unity.RenderPipelines.Core.Runtime.Shared.dll')
foreach ($packageName in $packageNames) {
    $packagePath = Join-Path $packageAssemblyDirectory $packageName
    if (-not (Test-Path -LiteralPath $packagePath)) { throw "缺少模板程序集：$packageName" }
    $references += '-reference:' + $packagePath
}
$runtimeSources = @(Get-ChildItem -LiteralPath (Join-Path $clientRoot 'Assets\Sandbox\Scripts') -Filter '*.cs' -Recurse | ForEach-Object { $_.FullName })
$runtimeAssembly = Join-Path $outputDirectory 'Qiongtu.Sandbox.SourceCheck.dll'
Invoke-SourceCompiler -CompilerArguments (@($baseArguments) + @($references) + @("-out:$runtimeAssembly") + @($runtimeSources)) -ResponseName 'runtime.rsp'
if ($LASTEXITCODE -ne 0) { throw '客户端运行时源码编译失败' }

$webRuntimeAssembly = Join-Path $outputDirectory 'Qiongtu.Sandbox.Web.SourceCheck.dll'
Invoke-SourceCompiler -CompilerArguments (@($baseArguments) + @($references) + @('-define:UNITY_WEBGL', "-out:$webRuntimeAssembly") + @($runtimeSources)) -ResponseName 'web-runtime.rsp'
if ($LASTEXITCODE -ne 0) { throw '网页客户端条件编译失败' }

$editorReferences = @($references)
$editorReferences += '-reference:' + $runtimeAssembly
if (-not (Test-Path -LiteralPath (Join-Path $unityModules 'UnityEditor.CoreModule.dll'))) {
    $editorReferences += '-reference:' + (Join-Path $unityManagedDirectory 'UnityEditor.dll')
}
$editorSources = @(Get-ChildItem -LiteralPath (Join-Path $clientRoot 'Assets\Sandbox\Editor') -Filter '*.cs' -Recurse | ForEach-Object { $_.FullName })
$editorAssembly = Join-Path $outputDirectory 'Qiongtu.Sandbox.Editor.SourceCheck.dll'
Invoke-SourceCompiler -CompilerArguments (@($baseArguments) + @($editorReferences) + @("-out:$editorAssembly") + @($editorSources)) -ResponseName 'editor.rsp'
if ($LASTEXITCODE -ne 0) { throw '客户端编辑器源码编译失败' }

$domainSource = Join-Path $clientRoot 'Assets\Sandbox\Scripts\Domain\SandboxSession.cs'
$armySource = Join-Path $clientRoot 'Assets\Sandbox\Scripts\Domain\SandboxArmy.cs'
$domainCheckSource = Join-Path $clientRoot 'Verification\DomainChecks.cs'
$domainAssembly = Join-Path $outputDirectory 'Qiongtu.Sandbox.DomainChecks.dll'
$domainReferences = @(Get-ChildItem -LiteralPath $netRuntimeDirectory -Filter '*.dll' | ForEach-Object { '-reference:' + $_.FullName })
Invoke-SourceCompiler -CompilerArguments (@('-nologo', '-noconfig', '-nostdlib+', '-langversion:9.0', '-warnaserror+', '-target:exe') + @($domainReferences) + @("-out:$domainAssembly", $domainSource, $armySource, $domainCheckSource)) -ResponseName 'domain.rsp'
if ($LASTEXITCODE -ne 0) { throw '前端状态验证程序编译失败' }
$runtimeConfiguration = '{"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"},"rollForward":"LatestMinor"}}'
Set-Content -LiteralPath (Join-Path $outputDirectory 'Qiongtu.Sandbox.DomainChecks.runtimeconfig.json') -Value $runtimeConfiguration -Encoding utf8
& $dotnetPath $domainAssembly
if ($LASTEXITCODE -ne 0) { throw '前端状态检查失败' }
$serverContracts = Join-Path $clientRoot 'Assets\Sandbox\Scripts\Domain\SandboxServerContracts.cs'
$serverChecks = Join-Path $clientRoot 'Verification\ServerProjectionChecks.cs'
$serverAssembly = Join-Path $outputDirectory 'Qiongtu.Sandbox.ServerChecks.dll'
Invoke-SourceCompiler -CompilerArguments (@('-nologo', '-noconfig', '-nostdlib+', '-langversion:9.0', '-warnaserror+', '-target:exe') + @($domainReferences) + @("-out:$serverAssembly", $domainSource, $armySource, $serverContracts, $serverChecks)) -ResponseName 'server-contracts.rsp'
if ($LASTEXITCODE -ne 0) { throw '后端地图投影检查编译失败' }
Set-Content -LiteralPath (Join-Path $outputDirectory 'Qiongtu.Sandbox.ServerChecks.runtimeconfig.json') -Value $runtimeConfiguration -Encoding utf8
& $dotnetPath $serverAssembly
if ($LASTEXITCODE -ne 0) { throw '后端地图投影检查失败' }
Write-Output '桌面运行时、网页条件分支、编辑器源码和独立状态检查均通过；原生导入、构建和浏览器画面须分别验收。'
