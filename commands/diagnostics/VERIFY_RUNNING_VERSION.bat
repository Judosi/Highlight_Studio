@echo off
setlocal EnableExtensions
chcp 65001 >nul
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
set "HS_PORT=8154"
if exist ".highlight_studio\runtime_port.txt" set /p HS_PORT=<".highlight_studio\runtime_port.txt"
echo Checking Highlight Studio on port %HS_PORT%...
powershell.exe -NoProfile -Command "$base='http://127.0.0.1:%HS_PORT%'; try { $h=Invoke-RestMethod -UseBasicParsing -Uri ($base + '/api/health') -TimeoutSec 5 -Headers @{'Cache-Control'='no-cache'}; $f=Invoke-RestMethod -UseBasicParsing -Uri ($base + '/release.json') -TimeoutSec 5 -Headers @{'Cache-Control'='no-cache'}; Write-Host ('Backend: ' + $h.app_version); Write-Host ('Frontend: ' + $f.app_version); Write-Host ('Design: ' + $f.design_id); if ($h.app_version -eq 'v11.2.7-quality-recovery-audit' -and $f.app_version -eq $h.app_version -and $f.design_id -eq 'studio-audited-v15') { Write-Host '[OK] Backend and frontend are both v11.2.7' -ForegroundColor Green; exit 0 } else { Write-Host '[ERROR] Backend/frontend release identity mismatch' -ForegroundColor Red; exit 2 } } catch { Write-Host ('[ERROR] Server is not verified: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }"
echo.
pause
endlocal
