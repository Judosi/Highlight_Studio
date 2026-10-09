param(
  [Parameter(Mandatory=$true)][string]$ArtifactsDir,
  [Parameter(Mandatory=$true)][string]$Version
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Unpacked = Join-Path $ArtifactsDir "win-unpacked"
$Resources = Join-Path $Unpacked "resources\app"
$Frontend = Join-Path $Resources "frontend\dist"
$Vendor = Join-Path $Resources "vendor"

$RequiredFiles = @(
  (Join-Path $Unpacked "Highlight Studio.exe"),
  (Join-Path $Resources "release_identity.json"),
  (Join-Path $Frontend "index.html"),
  (Join-Path $Frontend "build-manifest.json"),
  (Join-Path $Frontend "release.json"),
  (Join-Path $Frontend "studio-final-101513.css"),
  (Join-Path $Resources "engine\HighlightStudioEngine.exe"),
  (Join-Path $Vendor "ffmpeg\bin\ffmpeg.exe"),
  (Join-Path $Vendor "ffmpeg\bin\ffprobe.exe"),
  (Join-Path $Vendor "ffmpeg\SOURCE.txt"),
  (Join-Path $Vendor "twitchdownloadercli\TwitchDownloaderCLI.exe"),
  (Join-Path $Vendor "twitchdownloadercli\COPYRIGHT.txt"),
  (Join-Path $Vendor "twitchdownloadercli\THIRD-PARTY-LICENSES.txt"),
  (Join-Path $Vendor "aria2\aria2c.exe"),
  (Join-Path $Vendor "aria2\SOURCE.json")
)
foreach ($Path in $RequiredFiles) {
  if (!(Test-Path $Path -PathType Leaf)) { throw "Packaged desktop resource is missing: $Path" }
}
$FfmpegSource = Get-Content (Join-Path $Vendor "ffmpeg\SOURCE.txt") -Raw
if ($FfmpegSource -notmatch "Archive SHA256: [0-9a-fA-F]{64}" -or $FfmpegSource -notmatch "^Source: https://" ) {
  throw "Packaged FFmpeg provenance is missing an HTTPS source or verified archive checksum."
}

$Assets = Get-ChildItem (Join-Path $Frontend "assets") -File
if (!($Assets | Where-Object { $_.Name -match '^index-.*\.js$' })) { throw "Packaged frontend has no hashed JavaScript entrypoint." }
if (!($Assets | Where-Object { $_.Name -match '^index-.*\.css$' })) { throw "Packaged frontend has no hashed CSS entrypoint." }

$Identity = Get-Content (Join-Path $Resources "release_identity.json") -Raw | ConvertFrom-Json
$Release = Get-Content (Join-Path $Frontend "release.json") -Raw | ConvertFrom-Json
$BuildManifest = Get-Content (Join-Path $Frontend "build-manifest.json") -Raw | ConvertFrom-Json
if ([string]$Identity.version -ne $Version -or [string]$Release.version -ne $Version) {
  throw "Packaged release identity does not match Electron version $Version."
}
if ([string]$BuildManifest.identity.version -ne $Version -or !$BuildManifest.outputs.PSObject.Properties.Name) {
  throw "Packaged frontend build manifest is missing or inconsistent."
}
foreach ($Output in $BuildManifest.outputs.PSObject.Properties) {
  $Prefix = "frontend/dist/"
  if (!$Output.Name.StartsWith($Prefix)) { throw "Unexpected frontend output path in build manifest: $($Output.Name)" }
  $Relative = $Output.Name.Substring($Prefix.Length).Replace("/", "\")
  $OutputPath = Join-Path $Frontend $Relative
  if (!(Test-Path $OutputPath -PathType Leaf)) { throw "Manifest-declared frontend output is missing: $($Output.Name)" }
  $ActualHash = (Get-FileHash $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($ActualHash -ne [string]$Output.Value) { throw "Frontend output checksum mismatch: $($Output.Name)" }
}

$AppAsar = Join-Path $Unpacked "resources\app.asar"
if (!(Test-Path $AppAsar -PathType Leaf)) { throw "Packaged Electron app.asar is missing." }
& node (Join-Path $Root "tools\desktop\verify_electron_asar.mjs") $AppAsar
if ($LASTEXITCODE -ne 0) { throw "Packaged Electron app.asar security verification failed." }

& (Join-Path $Vendor "ffmpeg\bin\ffmpeg.exe") -version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { throw "Packaged FFmpeg failed its version smoke check." }
& (Join-Path $Vendor "ffmpeg\bin\ffprobe.exe") -version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { throw "Packaged FFprobe failed its version smoke check." }
& (Join-Path $Vendor "aria2\aria2c.exe") --version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { throw "Packaged aria2c failed its version smoke check." }
$TwitchStdout = Join-Path $env:RUNNER_TEMP "highlight-tdcli-help.stdout.txt"
$TwitchStderr = Join-Path $env:RUNNER_TEMP "highlight-tdcli-help.stderr.txt"
$TwitchProcess = Start-Process -FilePath (Join-Path $Vendor "twitchdownloadercli\TwitchDownloaderCLI.exe") -ArgumentList @("help") -NoNewWindow -Wait -PassThru -RedirectStandardOutput $TwitchStdout -RedirectStandardError $TwitchStderr
$TwitchExit = $TwitchProcess.ExitCode
$TwitchOutput = ((Get-Content $TwitchStdout -Raw -ErrorAction SilentlyContinue) + (Get-Content $TwitchStderr -Raw -ErrorAction SilentlyContinue))
Remove-Item $TwitchStdout, $TwitchStderr -Force -ErrorAction SilentlyContinue
if ($TwitchExit -notin @(0, 1) -or $TwitchOutput -notmatch "TwitchDownloaderCLI") {
  throw "Packaged TwitchDownloaderCLI failed its help smoke check with code $TwitchExit."
}

$AriaExpected = (Get-Content (Join-Path $Vendor "aria2\SOURCE.json") -Raw | ConvertFrom-Json).aria2c_sha256
$AriaActual = (Get-FileHash (Join-Path $Vendor "aria2\aria2c.exe") -Algorithm SHA256).Hash.ToLowerInvariant()
if ($AriaActual -ne [string]$AriaExpected) { throw "Packaged aria2c checksum does not match pinned provenance." }

Write-Host "Packaged layout verified: frontend assets, engine, FFmpeg/FFprobe, TwitchDownloaderCLI, and aria2c are present and runnable."
