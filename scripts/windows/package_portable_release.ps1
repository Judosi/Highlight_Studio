param(
  [Parameter(Mandatory=$true)][string]$ArtifactsDir,
  [Parameter(Mandatory=$true)][string]$OutputDir,
  [Parameter(Mandatory=$true)][string]$Version,
  [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"
$ArtifactsDir = (Resolve-Path $ArtifactsDir).Path
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir = (Resolve-Path $OutputDir).Path
$PortableExe = Join-Path $ArtifactsDir "Highlight-Studio-Portable-$Version-x64.exe"
if (!(Test-Path $PortableExe -PathType Leaf)) { throw "Portable Electron executable not found: $PortableExe" }

$Signature = Get-AuthenticodeSignature $PortableExe
if ($RequireSigned -and $Signature.Status -ne "Valid") { throw "Signed portable build required, signature is $($Signature.Status)." }
$BuildKind = if ($Signature.Status -eq "Valid") { "signed" } else { "unsigned-test" }
$Suffix = if ($BuildKind -eq "signed") { "signed" } else { "unsigned-test" }
$SignatureNotice = if ($BuildKind -eq "signed") {
  "EXE подписан Authenticode; проверьте издателя в свойствах файла."
} else {
  "Это тестовая unsigned-сборка. Windows SmartScreen может показать предупреждение."
}
$ArchiveName = "Highlight-Studio-$Version-Windows-x64-$Suffix.zip"
$ArchivePath = Join-Path $OutputDir $ArchiveName
$Stage = Join-Path $OutputDir ".portable-stage"
$PortableRoot = Join-Path $Stage "Highlight Studio $Version Portable"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PortableRoot | Out-Null

try {
  $EntryName = Split-Path $PortableExe -Leaf
  $EntryPath = Join-Path $PortableRoot $EntryName
  Copy-Item $PortableExe $EntryPath -Force
  $ReadmePath = Join-Path $PortableRoot "README_FIRST_RU.txt"
  @"
Highlight Studio $Version — portable Windows x64 ($BuildKind)

1. Полностью распакуйте ZIP в обычную папку.
2. Запустите $EntryName.
3. Node.js и Python устанавливать не требуется.

Пользовательские данные сохраняются отдельно:
- %LOCALAPPDATA%\HighlightStudio
- %USERPROFILE%\Videos\Highlight Studio

$SignatureNotice
Исходный GitHub ZIP не является готовой программой.
"@ | Set-Content -Path $ReadmePath -Encoding UTF8

  $Files = @()
  foreach ($Path in @($EntryPath, $ReadmePath)) {
    $Files += [ordered]@{
      path = Split-Path $Path -Leaf
      size = (Get-Item $Path).Length
      sha256 = (Get-FileHash $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
  }
  $Commit = if ($env:GITHUB_SHA) { $env:GITHUB_SHA } else { (& git rev-parse HEAD).Trim() }
  $Manifest = [ordered]@{
    schema_version = 1
    app = "Highlight Studio"
    version = $Version
    platform = "windows-x64"
    architecture = "x64"
    build_kind = $BuildKind
    signed = ($Signature.Status -eq "Valid")
    signature_status = [string]$Signature.Status
    entrypoint = $EntryName
    source_commit = $Commit
    generated_at_utc = [DateTime]::UtcNow.ToString("o")
    runtime = [ordered]@{
      shell = "Electron portable"
      backend = "PyInstaller standalone engine"
      frontend = "Vite production build embedded in Electron resources"
      bundled_tools = @("FFmpeg", "FFprobe", "yt-dlp", "TwitchDownloaderCLI", "aria2c")
    }
    files = $Files
  }
  $Manifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $PortableRoot "BUILD_MANIFEST.json") -Encoding UTF8
  if (Test-Path $ArchivePath) { Remove-Item $ArchivePath -Force }
  Compress-Archive -Path $PortableRoot -DestinationPath $ArchivePath -CompressionLevel Optimal
  Write-Host "Portable ZIP created: $ArchivePath"
  Write-Output $ArchivePath
} finally {
  Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
}
