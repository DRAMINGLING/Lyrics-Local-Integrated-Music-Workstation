$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$versionFile = Join-Path $projectRoot 'VERSION'
$version = (Get-Content -LiteralPath $versionFile -Raw).Trim()
if ($version -notmatch '^[A-Za-z0-9._-]+$') {
  throw "VERSION 只能包含字母、数字、点、下划线和连字符：$version"
}
$distRoot = Join-Path $projectRoot 'dist'
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
$archivePath = Join-Path $distRoot "Lyrics-$version-Windows.zip"
if (Test-Path -LiteralPath $archivePath) {
  Remove-Item -LiteralPath $archivePath -Force
}

$stream = $null
$archive = $null
$entryCount = 0
try {
  $stream = [System.IO.File]::Open($archivePath, [System.IO.FileMode]::CreateNew)
  $archive = [System.IO.Compression.ZipArchive]::new(
    $stream,
    [System.IO.Compression.ZipArchiveMode]::Create,
    $false
  )
  $files = Get-ChildItem -LiteralPath $projectRoot -Recurse -File -Force
  foreach ($file in $files) {
    $relative = $file.FullName.Substring($projectRoot.Length).TrimStart('\')
    $relativeSlash = $relative.Replace('\', '/')
    $leaf = [System.IO.Path]::GetFileName($relative)

    if ($relativeSlash -match '(^|/)(\.git|\.venv|__pycache__|\.pytest_cache|node_modules|dist)(/|$)' -or
        $relativeSlash -match '(^|/)\.runtime-mode$' -or
        $relativeSlash -match '\.pyc$' -or
        $relativeSlash -match '^auto-lrc-engine/upstream/(\.claude/|CLAUDE\.md$|demofile/)') {
      continue
    }
    if ($relativeSlash -match '^auto-lrc-engine/upstream/checkpoints/' -and $leaf -ne 'README.md') {
      continue
    }
    if ($relativeSlash -eq 'auto-lrc-engine/upstream/lid.176.ftz') {
      continue
    }
    if ($relativeSlash -match '^(input|output|covers)/' -and $leaf -ne '.gitkeep') {
      continue
    }
    if ($relativeSlash -match '^model-presets/' -and $leaf -notin @('README.md', 'LICENSE') -and
        [System.IO.Path]::GetExtension($leaf) -ne '.pt') {
      continue
    }

    $entryName = 'Lyrics repo/' + $relativeSlash
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
      $archive,
      $file.FullName,
      $entryName,
      [System.IO.Compression.CompressionLevel]::Optimal
    ) | Out-Null
    $entryCount++
  }
} finally {
  if ($null -ne $archive) { $archive.Dispose() }
  if ($null -ne $stream) { $stream.Dispose() }
}

$sizeMiB = [math]::Round((Get-Item -LiteralPath $archivePath).Length / 1MB, 2)
Write-Host "已生成 $archivePath（$sizeMiB MiB，$entryCount 个文件）。"
Write-Host '请检查发布说明与许可证，再将此 ZIP 附加到对应 GitHub Release。'
