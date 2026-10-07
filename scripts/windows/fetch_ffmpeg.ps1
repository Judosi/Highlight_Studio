param(
  [string]$Url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl-shared.zip",
  [string]$Sha256 = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Target = Join-Path $Root "vendor\ffmpeg"
$Bin = Join-Path $Target "bin"
$Ffmpeg = Join-Path $Bin "ffmpeg.exe"
$Ffprobe = Join-Path $Bin "ffprobe.exe"

if (!$Force -and (Test-Path $Ffmpeg) -and (Test-Path $Ffprobe)) {
  Write-Host "FFmpeg already exists: $Bin"
  exit 0
}

$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("highlight-ffmpeg-" + [guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "ffmpeg.zip"
$Extract = Join-Path $Temp "extract"
New-Item -ItemType Directory -Force -Path $Extract | Out-Null
New-Item -ItemType Directory -Force -Path $Target | Out-Null

try {
  Write-Host "Downloading FFmpeg..."
  Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
  if ($Sha256) {
    $Actual = (Get-FileHash -Path $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Sha256.Trim().ToLowerInvariant()) { throw "FFmpeg checksum mismatch. Expected $Sha256, got $Actual" }
  } else {
    Write-Warning "FFmpeg archive checksum was not pinned. Do not publish this build without verifying the exact archive."
  }
  Expand-Archive -Path $Zip -DestinationPath $Extract -Force
  $FoundFfmpeg = Get-ChildItem -Path $Extract -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
  $FoundFfprobe = Get-ChildItem -Path $Extract -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
  if (!$FoundFfmpeg -or !$FoundFfprobe) { throw "Archive does not contain ffmpeg.exe and ffprobe.exe" }
  $SourceBin = $FoundFfmpeg.Directory.FullName
  if (Test-Path $Bin) { Remove-Item $Bin -Recurse -Force }
  Copy-Item $SourceBin $Bin -Recurse -Force
  $LicenseFiles = Get-ChildItem -Path $Extract -Recurse -File | Where-Object { $_.Name -match '^(LICENSE|COPYING|README)' } | Select-Object -First 12
  $LicenseDir = Join-Path $Target "licenses"
  New-Item -ItemType Directory -Force -Path $LicenseDir | Out-Null
  foreach ($File in $LicenseFiles) { Copy-Item $File.FullName (Join-Path $LicenseDir $File.Name) -Force }
  @("Source: $Url", "Downloaded: $([DateTime]::UtcNow.ToString('o'))", "Build type: LGPL shared") | Set-Content (Join-Path $Target "SOURCE.txt") -Encoding UTF8
  & $Ffmpeg -version | Select-Object -First 1
  & $Ffprobe -version | Select-Object -First 1
  Write-Host "FFmpeg prepared: $Bin"
} finally {
  Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
