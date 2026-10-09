param([switch]$SkipTests,[switch]$SkipInstaller,[switch]$ReusePortable,[string]$InnoCompiler,[switch]$SkipRuntimeVerification)
$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $workspaceRoot
$pythonExe = Join-Path $workspaceRoot '.venv\Scripts\python.exe'
# Build with a controlled DLL search path: developer tools may ship an incompatible ICU.
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;" + (Split-Path $pythonExe)
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先创建构建虚拟环境，参见 README 的 Build from source。' }
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
if ($env:SEEKDEX_BUILD_MODEL_ROOT) {
    & $pythonExe scripts/prepare_preinstalled.py --ai-root $env:SEEKDEX_BUILD_MODEL_ROOT --ocr-root (Join-Path $env:SEEKDEX_BUILD_MODEL_ROOT 'ocr')
} else { & $pythonExe scripts/prepare_preinstalled.py }
if ($LASTEXITCODE -ne 0) { throw '预装模型准备或校验失败。' }
& $pythonExe scripts/package_release.py --version $productVersion
if ($LASTEXITCODE -ne 0) { throw 'Portable ZIP 生成失败。' }
if (-not $SkipInstaller) {
    if (-not $InnoCompiler) { $InnoCompiler = Join-Path $workspaceRoot '.tools\InnoSetup\ISCC.exe' }
    if (-not (Test-Path -LiteralPath $InnoCompiler)) { $InnoCompiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
    if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw '未找到 Inno Setup ISCC.exe，可传入 -InnoCompiler 完整路径。' }
    foreach ($edition in @('Standard','Preinstalled')) {
        & $InnoCompiler ("/DEdition=" + $edition) packaging/installer.iss
        if ($LASTEXITCODE -ne 0) { throw ('安装包构建失败：' + $edition) }
    }
}
& $pythonExe scripts/package_release.py --version $productVersion --hash-only
if ($LASTEXITCODE -ne 0) { throw '生成 SHA256 失败。' }
Get-ChildItem -LiteralPath (Join-Path $workspaceRoot 'release')
