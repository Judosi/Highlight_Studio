param(
  [Parameter(Mandatory=$true)][string]$EnginePath,
  [string]$PfxPath = $env:HIGHLIGHT_STUDIO_SIGN_PFX_PATH,
  [string]$PfxPassword = $env:HIGHLIGHT_STUDIO_SIGN_PFX_PASSWORD,
  [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
if (!$PfxPath) {
  Write-Warning "Engine signing skipped: HIGHLIGHT_STUDIO_SIGN_PFX_PATH is not configured."
  exit 0
}
if (!(Test-Path $EnginePath)) { throw "Engine binary not found: $EnginePath" }
if (!(Test-Path $PfxPath)) { throw "PFX file not found: $PfxPath" }
$Signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
if (!$Signtool) { throw "signtool.exe was not found. Install Windows SDK." }
& $Signtool sign /fd SHA256 /td SHA256 /tr $TimestampUrl /f $PfxPath /p $PfxPassword $EnginePath
if ($LASTEXITCODE -ne 0) { throw "signtool failed with code $LASTEXITCODE" }
$Signature = Get-AuthenticodeSignature -FilePath $EnginePath
if ($Signature.Status -ne 'Valid') { throw "Engine signature verification failed: $($Signature.Status)" }
Write-Host "Signed engine: $EnginePath"
