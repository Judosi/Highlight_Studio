# Highlight Studio v10.13.4 — этап 0

Версия закрывает emergency release blockers, найденные при аудите v10.13.3.

## Исправлено

- Секретные поля рекурсивно очищаются во всех settings-профилях и агрегированном dashboard response.
- Устаревшие OpenAI-поля удалены из frontend defaults.
- API-ключи, токены, пароли и другие секреты не сохраняются в `localStorage`.
- Старый `highlightStudioLocalToken` и профиль v998 автоматически очищаются при первом запуске.
- Production web frontend использует same-origin `/api`; localhost остаётся только для Vite development и desktop/file mode.
- `START_HERE.bat` до запуска проверяет обе команды `ffmpeg` и `ffprobe`.
- `commands/setup/INSTALL_FFMPEG.bat` использует Windows Package Manager или показывает официальный путь ручной установки.
- PyInstaller включает `onnxruntime`, native libraries и обе модели Silero VAD.
- Packaged-engine smoke проверяет VAD assets через реальный запущенный backend.
- Полная страница YouTube Publisher перенесена в `frontend/public` и воспроизводится после чистого `vite build`.
- POST-запросы отдельной страницы Publisher передают CSRF header в web-режиме.
- Версия синхронизирована как v10.13.4, release root — `highlight_studio_v10134_youtube_publisher`.
- Release manifest содержит SHA-256 каждого файла, версию и точное имя корневой папки.

## Обновлены runtime-зависимости

- FastAPI 0.140.11 и Starlette 1.3.1;
- python-multipart 0.0.32;
- requests 2.33.0;
- yt-dlp 2026.7.4;
- streamlink 8.4.0;
- cryptography 49.0.0;
- onnxruntime 1.28.0 закреплён явно.

`pip-audit` для `backend/requirements.txt` не находит известных уязвимостей на дату сборки.

## Проверки

- backend tests, Python lint/compile и dependency consistency;
- frontend contract/integration tests, lint и clean production build;
- Electron security contract tests;
- standalone engine build/startup, production UI, VAD runtime и graceful shutdown;
- полная проверка release ZIP и каждого SHA-256 из manifest.

Windows installer/portable EXE и подпись выпускаются отдельным Windows pipeline
`scripts/windows/build_hybrid_release.ps1`; исходный ZIP не выдаёт себя за подписанный installer.
