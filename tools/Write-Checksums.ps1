$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$version = (Get-Content -LiteralPath (Join-Path $projectRoot 'VERSION') -Raw).Trim()
if ($version -notmatch '^[A-Za-z0-9._-]+$') {
  throw "VERSION may contain only letters, numbers, dots, underscores, and hyphens: $version"
}

$distRoot = Join-Path $projectRoot 'dist'
$relativePaths = @(
  "Lyrics-Setup-$version-Windows-x64.exe",
  "Lyrics-$version-Windows.zip"
)
$checksumLines = foreach ($relativePath in $relativePaths) {
  $fullPath = Join-Path $distRoot $relativePath
  if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
    throw "Release artifact is missing: $fullPath"
  }
  $hash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
  "$hash *$relativePath"
}

$checksumPath = Join-Path $distRoot 'SHA256SUMS.txt'
[System.IO.File]::WriteAllLines(
  $checksumPath,
  [string[]]$checksumLines,
  [System.Text.UTF8Encoding]::new($false)
)
Write-Host "Created $checksumPath."
