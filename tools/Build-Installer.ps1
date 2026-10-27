param(
  [string]$CompilerPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$version = (Get-Content -LiteralPath (Join-Path $projectRoot 'VERSION') -Raw).Trim()
if ($version -notmatch '^[A-Za-z0-9._-]+$') {
  throw "VERSION may contain only letters, numbers, dots, underscores, and hyphens: $version"
}

$presetName = -join @([char]0x9884, [char]0x8bbe, '1.pt')
$presetRelativePath = Join-Path 'model-presets' $presetName
$requiredFiles = @(
  'installer\Lyrics.iss',
  'AutoLrcAligner.pyw',
  'InstallAutoLrc.cmd',
  'LaunchLyrics.vbs',
  'LICENSE',
  'model-presets\LICENSE',
  $presetRelativePath,
  'assets\Lyrics.ico',
  'auto-lrc-engine\setup.ps1'
)
foreach ($relativePath in $requiredFiles) {
  $fullPath = Join-Path $projectRoot $relativePath
  if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
    throw "Required installer input is missing: $fullPath"
  }
}

if (-not $CompilerPath) {
  $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
  $compilerCandidates = @(
    (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'),
    (Join-Path $programFilesX86 'Inno Setup 7\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $programFilesX86 'Inno Setup 6\ISCC.exe')
  )
  $compilerPathCandidate = $compilerCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -First 1
  if ($compilerPathCandidate) { $CompilerPath = [string]$compilerPathCandidate }
}

if (-not $CompilerPath) {
  $command = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
  if ($command) { $CompilerPath = $command.Source }
}

if (-not $CompilerPath -or -not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
  throw 'Inno Setup Compiler (ISCC.exe) was not found. Install Inno Setup 6.3 or 7 from https://jrsoftware.org/isinfo.php, then rerun this script.'
}

$compilerPathResolved = (Resolve-Path -LiteralPath $CompilerPath).Path
$languageFile = Join-Path (Split-Path -Parent $compilerPathResolved) 'Languages\ChineseSimplified.isl'
if (-not (Test-Path -LiteralPath $languageFile -PathType Leaf)) {
  throw "The selected Inno Setup installation is missing its Simplified Chinese language file: $languageFile"
}
$compilerVersion = (Get-Item -LiteralPath $compilerPathResolved).VersionInfo.ProductVersion
if ($compilerVersion -match '^(\d+)\.(\d+)' -and $matches[1] -eq '6' -and [int]$matches[2] -lt 3) {
  throw "Inno Setup 6.3 or newer is required for x64-compatible Windows support. Found $compilerVersion at $compilerPathResolved."
}
$installerScript = Join-Path $projectRoot 'installer\Lyrics.iss'
& $compilerPathResolved "/DAppVersion=$version" "/O$(Join-Path $projectRoot 'dist')" $installerScript
if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup compilation failed with exit code $LASTEXITCODE."
}

$installerPath = Join-Path $projectRoot "dist\Lyrics-Setup-$version-Windows-x64.exe"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
  throw "Inno Setup returned success, but the installer output is missing: $installerPath"
}
$sizeMiB = [math]::Round((Get-Item -LiteralPath $installerPath).Length / 1MB, 2)
Write-Host "Created $installerPath ($sizeMiB MiB)."
