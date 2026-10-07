param(
  [Parameter(Mandatory=$true)][string]$ArtifactsDir,
  [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"
$Files = Get-ChildItem -Path $ArtifactsDir -File | Where-Object { $_.Extension -in '.exe', '.msi' }
if (!$Files) { throw "No Windows artifacts found in $ArtifactsDir" }

$Failures = @()
foreach ($File in $Files) {
  $Hash = Get-FileHash -Path $File.FullName -Algorithm SHA256
  Write-Host "$($File.Name) SHA256=$($Hash.Hash)"
  $Signature = Get-AuthenticodeSignature -FilePath $File.FullName
  Write-Host "  Signature: $($Signature.Status) $($Signature.SignerCertificate.Subject)"
  if ($RequireSigned -and $Signature.Status -ne 'Valid') { $Failures += "$($File.Name): $($Signature.Status)" }
}
if ($Failures.Count -gt 0) { throw "Invalid or missing signatures: $($Failures -join '; ')" }
