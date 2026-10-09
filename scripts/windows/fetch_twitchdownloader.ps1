param(
  [string]$Url = "https://github.com/lay295/TwitchDownloader/releases/download/1.56.5/TwitchDownloaderCLI-1.56.5-Windows-x64.zip",
  [string]$Sha256 = "8b1b0695f2b1b6bf0d2535fab4b84032951cded8cf4078dfdf4d58e391c813a0",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
if (!$Sha256) { throw "TwitchDownloaderCLI download requires a pinned SHA-256 checksum." }

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Target = Join-Path $Root "vendor\twitchdownloadercli"
$Executable = Join-Path $Target "TwitchDownloaderCLI.exe"
$SourcePath = Join-Path $Target "SOURCE.json"

if (!$Force -and (Test-Path $Executable) -and (Test-Path $SourcePath)) {
  $Existing = Get-Content $SourcePath -Raw | ConvertFrom-Json
  if ([string]$Existing.archive_sha256 -eq $Sha256.ToLowerInvariant()) {
    Write-Host "Verified TwitchDownloaderCLI already exists: $Executable"
    exit 0
  }
}

$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("highlight-twitchdownloader-" + [guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "TwitchDownloaderCLI.zip"
$Extract = Join-Path $Temp "extract"
New-Item -ItemType Directory -Force -Path $Extract | Out-Null
New-Item -ItemType Directory -Force -Path $Target | Out-Null

try {
  Write-Host "Downloading checksum-pinned TwitchDownloaderCLI 1.56.5..."
  Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
  $Actual = (Get-FileHash -Path $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Actual -ne $Sha256.Trim().ToLowerInvariant()) {
    throw "TwitchDownloaderCLI checksum mismatch. Expected $Sha256, got $Actual"
  }
  Expand-Archive -Path $Zip -DestinationPath $Extract -Force
  $FoundExecutable = Get-ChildItem -Path $Extract -Recurse -Filter "TwitchDownloaderCLI.exe" | Select-Object -First 1
  if (!$FoundExecutable) { throw "TwitchDownloaderCLI archive does not contain TwitchDownloaderCLI.exe" }
  Copy-Item $FoundExecutable.FullName $Executable -Force
  foreach ($Name in @("COPYRIGHT.txt", "THIRD-PARTY-LICENSES.txt")) {
    $Found = Get-ChildItem -Path $Extract -Recurse -Filter $Name | Select-Object -First 1
    if (!$Found) { throw "TwitchDownloaderCLI archive is missing required notice: $Name" }
    Copy-Item $Found.FullName (Join-Path $Target $Name) -Force
  }
  $Source = [ordered]@{
    name = "TwitchDownloaderCLI"
    version = "1.56.5"
    source_url = $Url
    archive_sha256 = $Actual
    executable_sha256 = (Get-FileHash -Path $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
    architecture = "windows-x64"
    license = "GPL-3.0; bundled copyright and third-party notices"
  }
  $Source | ConvertTo-Json | Set-Content -Path $SourcePath -Encoding UTF8

  $Stdout = Join-Path $Temp "tdcli-help.stdout.txt"
  $Stderr = Join-Path $Temp "tdcli-help.stderr.txt"
  $HelpProcess = Start-Process -FilePath $Executable -ArgumentList @("help") -NoNewWindow -Wait -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
  $ExitCode = $HelpProcess.ExitCode
  $Output = ((Get-Content $Stdout -Raw -ErrorAction SilentlyContinue) + (Get-Content $Stderr -Raw -ErrorAction SilentlyContinue))
  if ($ExitCode -notin @(0, 1) -or $Output -notmatch "TwitchDownloaderCLI") {
    throw "TwitchDownloaderCLI smoke check failed with code $ExitCode.`n$Output"
  }
  Write-Host "TwitchDownloaderCLI prepared and verified: $Executable"
} finally {
  Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
