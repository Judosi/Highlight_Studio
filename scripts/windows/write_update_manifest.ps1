param(
  [Parameter(Mandatory=$true)][string]$ArtifactsDir,
  [Parameter(Mandatory=$true)][string]$Version
)

$ErrorActionPreference = "Stop"
$Installer = Get-ChildItem -Path $ArtifactsDir -File -Filter "Highlight-Studio-$Version-*.exe" |
  Where-Object { $_.Name -notmatch 'Portable' } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (!$Installer) { throw "NSIS installer not found in $ArtifactsDir" }

$Sha512Bytes = [System.Security.Cryptography.SHA512]::Create().ComputeHash([System.IO.File]::ReadAllBytes($Installer.FullName))
$Sha512Base64 = [Convert]::ToBase64String($Sha512Bytes)
$ReleaseDate = [DateTime]::UtcNow.ToString("o")
$Yaml = @"
version: $Version
files:
  - url: $($Installer.Name)
    sha512: $Sha512Base64
    size: $($Installer.Length)
path: $($Installer.Name)
sha512: $Sha512Base64
releaseDate: '$ReleaseDate'
"@
$Target = Join-Path $ArtifactsDir "latest.yml"
$Yaml | Set-Content -Path $Target -Encoding UTF8
Write-Host "Update manifest created: $Target"
