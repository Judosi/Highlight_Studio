param(
  [string]$Url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-08-13-05/ffmpeg-N-127252-ga25ba44c0c-win64-gpl-shared.zip",
  [string]$Sha256 = "418d52a70b96907141eb786da5ee3a29eed2c2c454420d52360d5330132d25ac",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
if (!$Sha256) { throw "FFmpeg download requires a pinned SHA-256 checksum." }
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
  $Actual = (Get-FileHash -Path $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Actual -ne $Sha256.Trim().ToLowerInvariant()) { throw "FFmpeg checksum mismatch. Expected $Sha256, got $Actual" }
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
  @("Source: $Url", "Archive SHA256: $Actual", "Downloaded: $([DateTime]::UtcNow.ToString('o'))", "Build type: GPL shared") | Set-Content (Join-Path $Target "SOURCE.txt") -Encoding UTF8
  $FfmpegOutput = & $Ffmpeg -version 2>&1 | Out-String
  $FfmpegExit = $LASTEXITCODE
  if ($FfmpegExit -ne 0) { throw "FFmpeg version smoke check failed with code $FfmpegExit.`n$FfmpegOutput" }
  $FfprobeOutput = & $Ffprobe -version 2>&1 | Out-String
  $FfprobeExit = $LASTEXITCODE
  if ($FfprobeExit -ne 0) { throw "FFprobe version smoke check failed with code $FfprobeExit.`n$FfprobeOutput" }
  $EncodeOutput = & $Ffmpeg -v error -f lavfi -i "color=c=black:s=16x16:d=0.1" -frames:v 1 -c:v libx264 -f null NUL 2>&1 | Out-String
  $EncodeExit = $LASTEXITCODE
  if ($EncodeExit -ne 0) { throw "Bundled FFmpeg must provide the libx264 encoder used by Highlight Studio (code $EncodeExit).`n$EncodeOutput" }
  Write-Host (($FfmpegOutput -split "`r?`n")[0])
  Write-Host (($FfprobeOutput -split "`r?`n")[0])
  $global:LASTEXITCODE = 0
  Write-Host "FFmpeg prepared: $Bin"
} finally {
  Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
