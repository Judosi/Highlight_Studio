# v9.2.4 — Production Start Fix

Главное исправление: приложение больше не зависит от Vite/Node.js при обычном запуске.

## Что изменено

- Frontend заранее собран в `frontend/dist` и отдаётся FastAPI backend'ом.
- `run_windows.bat` теперь запускает только backend + готовый UI на `http://127.0.0.1:8000`.
- Ошибки вида `vite не является командой`, `react could not be resolved`, `lucide-react could not be resolved` больше не должны мешать обычному запуску.
- `start_frontend.bat` оставлен только для dev-режима.
- `package.json` переведён на стабильные версии React/Vite.
- Все функции v9.2.3 сохранены: Ollama Only, Twitch Import, Prepare Twitch Source, Fast Import, IRL, OCR, visual scan, AI 100%, metadata, render, export.

## Как запускать

1. Распакуй архив в новую папку.
2. Запусти `run_windows.bat`.
3. Открой `http://127.0.0.1:8000`.

Node.js теперь нужен только если ты хочешь разрабатывать frontend.
