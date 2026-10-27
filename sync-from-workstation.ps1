$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
$workspaceRoot = Split-Path -Parent $packageRoot
$sourceCandidate = Join-Path $workspaceRoot 'Lyrics'
if (-not (Test-Path -LiteralPath $sourceCandidate -PathType Container)) {
  throw "本地工作台目录不存在：$sourceCandidate"
}
$sourceRoot = (Resolve-Path -LiteralPath $sourceCandidate).Path.TrimEnd('\')
if ($sourceRoot -eq $packageRoot) {
  throw '同步源和目标不能是同一个目录。'
}

$copied = 0
$skipped = 0
$sourceFiles = Get-ChildItem -LiteralPath $sourceRoot -Recurse -File -Force
foreach ($sourceFile in $sourceFiles) {
  $relative = $sourceFile.FullName.Substring($sourceRoot.Length).TrimStart('\')
  $relativeSlash = $relative.Replace('\', '/')
  $leaf = [System.IO.Path]::GetFileName($relative)

  if ($relativeSlash -match '(^|/)(\.git|\.venv|__pycache__|\.pytest_cache|node_modules)(/|$)' -or
      $relativeSlash -match '(^|/)\.runtime-mode$' -or
      $relativeSlash -match '\.pyc$' -or
      $relativeSlash -match '^auto-lrc-engine/upstream/(\.claude/|CLAUDE\.md$|demofile/)' -or
      $relativeSlash -eq 'auto-lrc-engine/upstream/lid.176.ftz') {
    $skipped++
    continue
  }
  if ($relativeSlash -match '^auto-lrc-engine/upstream/checkpoints/' -and $leaf -ne 'README.md') {
    $skipped++
    continue
  }
  if ($relativeSlash -match '^(input|output|covers)/' -and $leaf -ne '.gitkeep') {
    $skipped++
    continue
  }
  if ($relativeSlash -match '^model-presets/' -and $leaf -notin @('README.md', 'LICENSE') -and
      [System.IO.Path]::GetExtension($leaf) -ne '.pt') {
    continue
  }

  $destination = Join-Path $packageRoot $relative
  $destinationDirectory = Split-Path -Parent $destination
  if (-not (Test-Path -LiteralPath $destinationDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
  }
  Copy-Item -LiteralPath $sourceFile.FullName -Destination $destination -Force
  $copied++
}
Write-Host "已从本地 Lyrics 同步 $copied 个文件；跳过 $skipped 个环境、模型或用户媒体文件。"
Write-Host '发布仓库的 .git、tools、版本文件和 GitHub 配置保持不变。'
