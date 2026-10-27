param(
  [switch]$Cuda
)

$ErrorActionPreference = 'Stop'
$engineRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvRoot = Join-Path $engineRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
$runtimeModeFile = Join-Path $engineRoot '.runtime-mode'
$requestedMode = if ($Cuda) { 'cuda' } else { 'cpu' }
$environmentExisted = Test-Path -LiteralPath $venvPython

if (-not (Test-Path -LiteralPath $venvPython)) {
  $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  $created = $false

  if ($pythonLauncher) {
    foreach ($version in @('3.10', '3.11')) {
      # A missing runtime is an expected probe result. In Windows PowerShell
      # 5.1, native stderr becomes an error record and Stop would abort here.
      $previousErrorActionPreference = $ErrorActionPreference
      $ErrorActionPreference = 'Continue'
      try {
        & $pythonLauncher.Source "-$version" -c "import struct,sys;sys.exit(0 if struct.calcsize('P') == 8 else 1)" *> $null
        $launcherExitCode = $LASTEXITCODE
      } finally {
        $ErrorActionPreference = $previousErrorActionPreference
      }
      if ($launcherExitCode -eq 0) {
        & $pythonLauncher.Source "-$version" -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw "创建 Python $version 环境失败。" }
        $created = $true
        break
      }
    }
  }

  if (-not $created -and $pythonCommand) {
    $pythonVersion = (& $pythonCommand.Source --version 2>&1 | Out-String).Trim()
    if ($pythonVersion -match 'Python 3\.(10|11)\.') {
      $pythonCommandBitness = (& $pythonCommand.Source -c "import struct; print(struct.calcsize('P') * 8)")
      if ($LASTEXITCODE -eq 0 -and ($pythonCommandBitness | Out-String).Trim() -eq '64') {
        & $pythonCommand.Source -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败。' }
        $created = $true
      }
    }
  }

  if (-not $created) {
    throw '需要先安装 64 位 Python 3.10 或 3.11，并启用 Python Launcher（py）。'
  }
}

$pythonBitness = (& $venvPython -c "import struct; print(struct.calcsize('P') * 8)")
if ($LASTEXITCODE -ne 0 -or ($pythonBitness | Out-String).Trim() -ne '64') {
  throw '需要 64 位 Python 3.10 或 3.11。请安装 64 位版本后重新运行安装程序。'
}

$installedMode = if (Test-Path -LiteralPath $runtimeModeFile) {
  (Get-Content -LiteralPath $runtimeModeFile -Raw).Trim().ToLowerInvariant()
} else {
  ''
}
$runtimeModeChanged = -not $environmentExisted -or $installedMode -ne $requestedMode

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw '更新 pip 失败。' }

if ($runtimeModeChanged) {
  $torchIndex = 'https://download.pytorch.org/whl/cpu'
  if ($Cuda) {
    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $nvidiaSmi) {
      throw 'CUDA 安装需要可用的 NVIDIA 驱动。需要 CPU 版时请直接运行 InstallAutoLrc.cmd。'
    }
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
      & $nvidiaSmi.Source -L *> $null
      $driverExitCode = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($driverExitCode -ne 0) { throw '没有检测到可用的 NVIDIA GPU。' }
    $torchIndex = 'https://download.pytorch.org/whl/cu121'
  }

  Write-Host "安装 PyTorch 运行时：$torchIndex"
  $torchInstallArgs = @('install', '--force-reinstall', '--index-url', $torchIndex, 'torch==2.1.2', 'torchaudio==2.1.2')
  & $venvPython -m pip @torchInstallArgs
  if ($LASTEXITCODE -ne 0) { throw "安装 PyTorch 失败（$torchIndex）。请检查网络、显卡驱动后重新运行。" }
}

Write-Host '安装或更新 Auto-LRC 依赖…'
& $venvPython -m pip install -r (Join-Path $engineRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '安装 Auto-LRC 依赖失败。请查看上方错误并重新运行安装程序。' }
[System.IO.File]::WriteAllText($runtimeModeFile, $requestedMode, [System.Text.Encoding]::ASCII)

Write-Host ''
Write-Host '安装完成。Auto-LRC 声学模型会在首次打轴时下载；人声分离模型仅在确认许可并启用后下载。'
