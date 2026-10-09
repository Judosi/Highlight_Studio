param(
  [Parameter(Mandatory=$true)][string]$ArchivePath,
  [Parameter(Mandatory=$true)][string]$ArtifactsDir,
  [Parameter(Mandatory=$true)][string]$Version
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ArchivePath = (Resolve-Path $ArchivePath).Path
$ArtifactsDir = (Resolve-Path $ArtifactsDir).Path
$TestRoot = Join-Path $env:RUNNER_TEMP "Highlight Studio проверка portable"
$ExtractRoot = Join-Path $TestRoot "Распакованная программа с пробелами"
$DataDir = Join-Path $TestRoot "Данные пользователя"
$ProjectsDir = Join-Path $TestRoot "Видео пользователя\Highlight Studio"
$TempDir = Join-Path $TestRoot "Временные файлы"
$ReportDir = Join-Path $Root "build\desktop\portable-smoke"
$Port = 0
$Process = $null

function Get-FreePort {
  $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $Listener.Start()
  $Selected = ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
  $Listener.Stop()
  return $Selected
}

function Wait-ForHealth([int]$SelectedPort, [int]$TimeoutSeconds = 90) {
  $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTime]::UtcNow -lt $Deadline) {
    try {
      $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$SelectedPort/api/health" -TimeoutSec 2
      if ($Health.ok) { return $Health }
    } catch {}
    Start-Sleep -Milliseconds 500
  }
  throw "Portable Electron backend did not become healthy in $TimeoutSeconds seconds."
}

function Start-Portable([string]$Executable, [int]$SelectedPort) {
  $SavedPath = $env:PATH
  $SavedTemp = $env:TEMP
  $SavedTmp = $env:TMP
  try {
    # The child can see Windows system tools, but not the runner's Python or Node.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:TEMP = $TempDir
    $env:TMP = $TempDir
    $env:HIGHLIGHT_STUDIO_DATA_DIR = $DataDir
    $env:HIGHLIGHT_STUDIO_PROJECTS_DIR = $ProjectsDir
    $env:HIGHLIGHT_STUDIO_PORT = [string]$SelectedPort
    $env:HIGHLIGHT_STUDIO_LOG_LEVEL = "warning"
    $script:Process = Start-Process -FilePath $Executable -WorkingDirectory (Split-Path $Executable) -PassThru
  } finally {
    $env:PATH = $SavedPath
    $env:TEMP = $SavedTemp
    $env:TMP = $SavedTmp
  }
}

function Stop-PortableGracefully {
  $Closed = $false
  $WindowProcess = $null
  for ($Attempt = 0; $Attempt -lt 40 -and !$Closed; $Attempt += 1) {
    $Candidates = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -eq "Highlight Studio" }
    $WindowProcess = $Candidates | Select-Object -First 1
    if ($WindowProcess) { $Closed = $WindowProcess.CloseMainWindow() }
    if (!$Closed) { Start-Sleep -Milliseconds 500 }
  }
  if (!$Closed) { throw "Portable Electron did not expose a closeable main window." }
  if ($WindowProcess -and !$WindowProcess.WaitForExit(30000)) { throw "Portable Electron window process did not exit within 30 seconds." }
  $Deadline = [DateTime]::UtcNow.AddSeconds(30)
  $Stopped = $false
  while ([DateTime]::UtcNow -lt $Deadline) {
    try {
      Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1 | Out-Null
    } catch {
      $Stopped = $true
      break
    }
    Start-Sleep -Milliseconds 300
  }
  if (!$Stopped) { throw "Portable Electron backend did not stop after the window was closed." }
  $SessionPath = Join-Path $DataDir "session_state.json"
  if (!(Test-Path $SessionPath)) { throw "Portable backend did not write session_state.json." }
  $Session = Get-Content $SessionPath -Raw | ConvertFrom-Json
  if ($Session.clean_shutdown -ne $true) { throw "Portable backend did not record a clean shutdown." }
  $Remaining = Get-Process -Name "HighlightStudioEngine" -ErrorAction SilentlyContinue
  if ($Remaining) { throw "Packaged backend process remained after Electron shutdown." }
}

if (Test-Path $TestRoot) { Remove-Item $TestRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ExtractRoot, $DataDir, $ProjectsDir, $TempDir, $ReportDir | Out-Null

try {
  Expand-Archive -Path $ArchivePath -DestinationPath $ExtractRoot -Force
  $Executable = Get-ChildItem -Path $ExtractRoot -Recurse -Filter "Highlight-Studio-Portable-$Version-x64.exe" | Select-Object -First 1
  if (!$Executable) { throw "Extracted ZIP does not contain the expected portable executable." }
  if ($Executable.FullName -notmatch "[А-Яа-яЁё]" -or $Executable.FullName -notmatch " ") {
    throw "Smoke extraction path must contain both Cyrillic characters and spaces."
  }

  $PackagedFfmpeg = Join-Path $ArtifactsDir "win-unpacked\resources\app\vendor\ffmpeg\bin\ffmpeg.exe"
  if (!(Test-Path $PackagedFfmpeg)) { throw "Packaged FFmpeg is unavailable for smoke fixture generation." }
  $SampleVideo = Join-Path $TestRoot "тестовое видео с пробелами.mp4"
  & $PackagedFfmpeg -hide_banner -loglevel error -f lavfi -i "color=c=black:s=320x180:d=1" -f lavfi -i "anullsrc=r=48000:cl=stereo" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac -y $SampleVideo
  if ($LASTEXITCODE -ne 0 -or !(Test-Path $SampleVideo)) { throw "Failed to create a deterministic smoke-test video." }

  $Port = Get-FreePort
  Start-Portable $Executable.FullName $Port
  $FirstHealth = Wait-ForHealth $Port
  $TokenPath = Join-Path $DataDir "local_auth_token.txt"
  if (!(Test-Path $TokenPath)) { throw "Portable backend did not create its local API token." }
  $Headers = @{ "X-Local-Token" = (Get-Content $TokenPath -Raw).Trim() }
  $Payload = @{ source_path = $SampleVideo; storage_mode = "reference" } | ConvertTo-Json
  $Created = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/projects/from-path" -Headers $Headers -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($Payload))
  if (!$Created.id) { throw "Portable API did not create a test project." }
  $ProjectId = [string]$Created.id
  Stop-PortableGracefully

  $Port = Get-FreePort
  Start-Portable $Executable.FullName $Port
  $SecondHealth = Wait-ForHealth $Port
  $Headers = @{ "X-Local-Token" = (Get-Content $TokenPath -Raw).Trim() }
  $Reopened = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/projects/$ProjectId" -Headers $Headers -TimeoutSec 10
  if ([string]$Reopened.id -ne $ProjectId) { throw "Portable API did not reopen the persisted test project after restart." }
  Stop-PortableGracefully

  $Report = [ordered]@{
    ok = $true
    version = $Version
    archive = Split-Path $ArchivePath -Leaf
    extraction_path_had_spaces_and_cyrillic = $true
    system_python_or_node_on_child_path = $false
    frontend_and_api_started = $true
    project_created = $ProjectId
    project_reopened_after_restart = $true
    clean_shutdown_after_each_run = $true
    first_health_version = [string]$FirstHealth.app_version
    second_health_version = [string]$SecondHealth.app_version
  }
  $Report | ConvertTo-Json | Set-Content (Join-Path $ReportDir "portable-smoke-report.json") -Encoding UTF8
  Write-Host "Portable ZIP smoke test passed from a Cyrillic path with spaces; project $ProjectId survived restart."
} finally {
  if ($Process -and !$Process.HasExited) { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue }
  $DesktopLog = Join-Path $DataDir "logs\desktop.log"
  if (Test-Path $DesktopLog) { Copy-Item $DesktopLog (Join-Path $ReportDir "desktop.log") -Force }
}
