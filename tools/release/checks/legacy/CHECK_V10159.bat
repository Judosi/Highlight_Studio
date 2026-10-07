@echo off
setlocal
set "ERR=0"
echo Checking Highlight Studio v10.15.9 semantic-quality package...
if not exist "release_identity.json" echo [ERROR] release_identity.json missing & set "ERR=1"
if not exist "frontend\dist\index.html" echo [ERROR] frontend dist missing & set "ERR=1"
if not exist "frontend\dist\studio-final-10159.css" echo [ERROR] studio-final-10159.css missing & set "ERR=1"
if not exist "backend\src\highlight_studio\services\pipeline.py" echo [ERROR] pipeline missing & set "ERR=1"
if not exist "tests\test_semantic_quality_10159.py" echo [ERROR] semantic quality regression tests missing & set "ERR=1"
if not exist "tests\test_manual_review_navigation_10158.py" echo [ERROR] manual review regression tests missing & set "ERR=1"
if not exist "frontend\tests\semanticQuality10159.contract.test.js" echo [ERROR] frontend semantic quality contract missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.9 semantic-quality files are present.
exit /b %ERR%
