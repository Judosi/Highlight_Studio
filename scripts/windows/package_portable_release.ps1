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
$SignatureNoticeB64 = if ($BuildKind -eq "signed") {
  "RVhFINC/0L7QtNC/0LjRgdCw0L0gQXV0aGVudGljb2RlOyDQv9GA0L7QstC10YDRjNGC0LUg0LjQt9C00LDRgtC10LvRjyDQsiDRgdCy0L7QudGB0YLQstCw0YUg0YTQsNC50LvQsC4="
} else {
  "0K3RgtC+INGC0LXRgdGC0L7QstCw0Y8gdW5zaWduZWQt0YHQsdC+0YDQutCwLiBXaW5kb3dzIFNtYXJ0U2NyZWVuINC80L7QttC10YIg0L/QvtC60LDQt9Cw0YLRjCDQv9GA0LXQtNGD0L/RgNC10LbQtNC10L3QuNC1Lg=="
}
$SignatureNotice = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($SignatureNoticeB64))
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
  $ReadmeTemplateB64 = "SGlnaGxpZ2h0IFN0dWRpbyB7MH0g4oCUIHBvcnRhYmxlIFdpbmRvd3MgeDY0ICh7MX0pCgoxLiDQn9C+0LvQvdC+0YHRgtGM0Y4g0YDQsNGB0L/QsNC60YPQudGC0LUgWklQINCyINC+0LHRi9GH0L3Rg9GOINC/0LDQv9C60YMuCjIuINCX0LDQv9GD0YHRgtC40YLQtSB7Mn0uCjMuIE5vZGUuanMg0LggUHl0aG9uINGD0YHRgtCw0L3QsNCy0LvQuNCy0LDRgtGMINC90LUg0YLRgNC10LHRg9C10YLRgdGPLgoK0J/QvtC70YzQt9C+0LLQsNGC0LXQu9GM0YHQutC40LUg0LTQsNC90L3Ri9C1INGB0L7RhdGA0LDQvdGP0Y7RgtGB0Y8g0L7RgtC00LXQu9GM0L3QvjoKLSAlTE9DQUxBUFBEQVRBJVxIaWdobGlnaHRTdHVkaW8KLSAlVVNFUlBST0ZJTEUlXFZpZGVvc1xIaWdobGlnaHQgU3R1ZGlvCgp7M30K0JjRgdGF0L7QtNC90YvQuSBHaXRIdWIgWklQINC90LUg0Y/QstC70Y/QtdGC0YHRjyDQs9C+0YLQvtCy0L7QuSDQv9GA0L7Qs9GA0LDQvNC80L7QuS4K"
  $ReadmeTemplate = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($ReadmeTemplateB64))
  $Readme = $ReadmeTemplate -f $Version, $BuildKind, $EntryName, $SignatureNotice
  [System.IO.File]::WriteAllText($ReadmePath, $Readme, [System.Text.UTF8Encoding]::new($false))

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
