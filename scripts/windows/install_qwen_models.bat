@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
where ollama >nul 2>nul
if errorlevel 1 (
    echo ERROR: Ollama is not installed or not added to PATH.
    echo Install Ollama from: https://ollama.com/download
    pause
    exit /b 1
)
ollama pull qwen3:8b
ollama pull qwen3-vl:8b
echo Optional:
echo ollama pull qwen2.5:7b
echo ollama pull qwen2.5:3b
pause
