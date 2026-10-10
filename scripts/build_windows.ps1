param([switch]$SkipTests,[switch]$SkipInstaller,[switch]$ReusePortable,[string]$InnoCompiler,[switch]$SkipRuntimeVerification,
      [string]$PythonExe,[ValidateSet('Standard','Preinstalled','All')][string]$Edition='Standard')
$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $workspaceRoot
if (-not $PythonExe) { $PythonExe = Join-Path $workspaceRoot '.venv\Scripts\python.exe' }
$pythonExe = [IO.Path]::GetFullPath($PythonExe)
$gitDirectory = Split-Path (Get-Command git -ErrorAction Stop).Source
$env:PYTHONPATH = Join-Path $workspaceRoot 'src'
# Build with a controlled DLL search path: developer tools may ship an incompatible ICU.
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$gitDirectory;" + (Split-Path $pythonExe)
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先创建构建虚拟环境，参见 README 的 Build from source。' }
& $pythonExe scripts/release_checks.py --preflight
if ($LASTEXITCODE -ne 0) { throw '发行源码或构建环境不一致，停止构建。' }
if (-not $SkipTests) {
    $testDirectory = Join-Path $workspaceRoot ('.verification\pytest-build-' + [Guid]::NewGuid().ToString('N'))
    & $pythonExe -m pytest -q --basetemp $testDirectory -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw '测试失败，停止构建。' }
}
if (-not $ReusePortable) {
    foreach ($name in @('build','dist')) {
        $target = [IO.Path]::GetFullPath((Join-Path $workspaceRoot $name))
        if (-not $target.StartsWith($workspaceRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw '构建清理路径无效。' }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    }
    & $pythonExe scripts/build_resources.py
    if ($LASTEXITCODE -ne 0) { throw '资源生成失败。' }
    & $pythonExe -m PyInstaller --noconfirm --clean packaging/Seekdex.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE 构建失败。' }
}
$portableExe = Join-Path $workspaceRoot 'dist\Seekdex\Seekdex.exe'
if (-not (Test-Path -LiteralPath $portableExe)) { throw 'Portable EXE 不存在。' }
& $pythonExe scripts/release_licenses.py --stage
if ($LASTEXITCODE -ne 0) { throw 'GPL 正文、通知或源码归档校验失败。' }
& $pythonExe scripts/release_checks.py --verify-exe
if ($LASTEXITCODE -ne 0) { throw '实际 EXE 版本、许可证、主页或源码提交不一致。' }
New-Item -ItemType Directory -Force -Path (Join-Path $workspaceRoot 'release') | Out-Null
# Portable smoke-test calls the EXE itself, never python -m.
$checkRoot = Join-Path $workspaceRoot ('.verification\frozen-build-' + [Guid]::NewGuid().ToString('N'))
$previousProfile = $env:SEEKDEX_HOME
if (-not $SkipRuntimeVerification) { try {
    $env:SEEKDEX_HOME = Join-Path $checkRoot 'profile'
    $check = Start-Process -FilePath $portableExe -ArgumentList '--verify-runtime',('"'+$checkRoot+'"') -WindowStyle Hidden -PassThru
    if (-not $check.WaitForExit(180000)) { Stop-Process -Id $check.Id; throw 'Portable 验证超时。' }
    if ($check.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $checkRoot 'report.json'))) { throw 'Portable 验证失败。' }
    $report = Get-Content -LiteralPath (Join-Path $checkRoot 'report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.error) { throw ('Portable 验证失败：'+$report.error) }
} finally { $env:SEEKDEX_HOME = $previousProfile } }
else { Write-Warning 'Runtime verification was explicitly skipped. These artifacts are not verified for release.' }
$productVersion = & $pythonExe -c 'from seekdex.app_info import VERSION; print(VERSION)'
# A build-only override reuses verified caches without migrating the real profile.
if ($Edition -ne 'Standard') { if ($env:SEEKDEX_BUILD_MODEL_ROOT) {
    & $pythonExe scripts/prepare_preinstalled.py --ai-root $env:SEEKDEX_BUILD_MODEL_ROOT --ocr-root (Join-Path $env:SEEKDEX_BUILD_MODEL_ROOT 'ocr')
} else { & $pythonExe scripts/prepare_preinstalled.py }
if ($LASTEXITCODE -ne 0) { throw '预装模型准备或校验失败。' } }
& $pythonExe scripts/package_release.py --version $productVersion --edition $Edition.ToLowerInvariant()
if ($LASTEXITCODE -ne 0) { throw 'Portable ZIP 生成失败。' }
if (-not $SkipInstaller) {
    if (-not $InnoCompiler) { $InnoCompiler = Join-Path $workspaceRoot '.tools\InnoSetup\ISCC.exe' }
    if (-not (Test-Path -LiteralPath $InnoCompiler)) { $InnoCompiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
    if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw '未找到 Inno Setup ISCC.exe，可传入 -InnoCompiler 完整路径。' }
    $buildEditions = if ($Edition -eq 'All') { @('Standard','Preinstalled') } else { @($Edition) }
    foreach ($buildEdition in $buildEditions) {
        & $InnoCompiler ("/DEdition=" + $buildEdition) packaging/installer.iss
        if ($LASTEXITCODE -ne 0) { throw ('安装包构建失败：' + $buildEdition) }
        if ($buildEdition -eq 'Standard') {
            $installerPath = Join-Path $workspaceRoot ('release\Seekdex-' + $productVersion + '-Windows-x64-Setup.exe')
            & $pythonExe scripts/release_checks.py --record-installer $installerPath
            if ($LASTEXITCODE -ne 0) { throw '安装器版本、许可证、主页或源码提交不一致。' }
        }
    }
}
$finalOptions = if ($SkipInstaller) { @('--portable-only') } else { @() }
& $pythonExe scripts/package_release.py --version $productVersion --hash-only --edition $Edition.ToLowerInvariant() @finalOptions
if ($LASTEXITCODE -ne 0) { throw '生成 SHA256 失败。' }
Get-ChildItem -LiteralPath (Join-Path $workspaceRoot 'release')
