# Highlight Studio v10.5.1 — Release Candidate

## Назначение версии

v10.5.1 закрывает первый этап подготовки Hybrid Desktop к ограниченной платной beta. Основная цель — не новые функции монтажа, а безопасная установка, первый запуск, восстановление, обновления и диагностика.

## Что реализовано

### Первый запуск

- пошаговый мастер первого запуска;
- проверка FFmpeg/FFprobe и faster-whisper;
- выбор пользовательской папки проектов;
- проверка Ollama и рекомендуемой модели;
- явные Windows-команды установки Ollama и загрузки модели только после действия пользователя;
- сохранение завершённого onboarding;
- возможность снова открыть мастер из меню.

### Миграции и сохранность проектов

- введена версия схемы проекта;
- старые `project.json` мигрируются автоматически и идемпотентно;
- сохраняется история выполненных миграций;
- создаётся сводный отчёт о миграциях;
- обновление приложения не должно удалять проекты и готовые ролики.

### Восстановление после аварии

- фиксируется начало и чистое завершение desktop-сеанса;
- после некорректного закрытия незавершённые проекты помечаются как прерванные и восстанавливаемые;
- существующая очередь задач очищается от ложного состояния `running`;
- Electron предупреждает при закрытии во время активной задачи;
- Electron запрашивает защищённое мягкое завершение Uvicorn и ждёт сохранения состояния;
- принудительный `taskkill` используется только как fallback после таймаута;
- пользователь может оставить приложение работать либо остановить его с сохранением checkpoint.

### Поддержка и диагностика

- кнопка создания ZIP-отчёта для поддержки;
- отчёт содержит версии, системную проверку, миграции, восстановление и хвосты логов;
- токены, cookies, ключи, личные пути и другие секреты редактируются;
- видео, аудио и транскрипты не добавляются в отчёт;
- архив сохраняется локально и отправляется только самим пользователем.

### Обновления

- подключён `electron-updater`;
- поддерживается только generic HTTPS update feed; HTTP и URL со встроенными учётными данными блокируются;
- приложение проверяет обновление после запуска и затем периодически;
- загрузка и установка выполняются только через ограниченный preload API;
- для installer автоматически формируется `latest.yml` с SHA-512 и размером;
- update URL задаётся в release channel или через окружение;
- каналы можно расширить на Stable/Beta без изменения backend.

### Windows release pipeline

- отдельные production и test virtual environments;
- standalone engine собирается PyInstaller без тестовых зависимостей;
- после сборки engine реально запускается и обязан сообщить ту же версию, что installer;
- FFmpeg для релиза может быть загружен только по закреплённому URL и SHA-256;
- поддерживается подпись engine и Electron/NSIS одним PFX-сертификатом через секреты CI;
- подписанный tag build требует сертификат, update URL и закреплённый FFmpeg;
- installer, portable EXE и update manifest проверяются после сборки.

## Команда Windows-сборки

Тестовая неподписанная сборка:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/build_hybrid_release.ps1 `
  -AllowUnverifiedFfmpegDownload
```

Release-сборка должна использовать закреплённый FFmpeg, HTTPS update URL и сертификат:

```powershell
$env:HIGHLIGHT_STUDIO_FFMPEG_URL = "https://example.com/ffmpeg.zip"
$env:HIGHLIGHT_STUDIO_FFMPEG_SHA256 = "<SHA256>"
$env:HIGHLIGHT_STUDIO_UPDATE_URL = "https://updates.example.com/highlight-studio"
$env:HIGHLIGHT_STUDIO_SIGN_PFX_PATH = "C:\secure\publisher.pfx"
$env:HIGHLIGHT_STUDIO_SIGN_PFX_PASSWORD = "<secret>"
$env:CSC_LINK = $env:HIGHLIGHT_STUDIO_SIGN_PFX_PATH
$env:CSC_KEY_PASSWORD = $env:HIGHLIGHT_STUDIO_SIGN_PFX_PASSWORD

powershell -ExecutionPolicy Bypass -File scripts/windows/build_hybrid_release.ps1 -RequireSigned
```

Артефакты:

```text
build/desktop/installer/Highlight-Studio-Setup-10.5.1-x64.exe
build/desktop/installer/Highlight-Studio-Portable-10.5.1-x64.exe
build/desktop/installer/latest.yml
```

## Что подтверждено в текущей среде

- backend, frontend и Electron tests;
- lint/format/build;
- npm dependency audits;
- настоящий FFmpeg render;
- сборка standalone engine;
- запуск собранного engine без системного Python;
- health API версии v10.5.1;
- встроенные yt-dlp/Streamlink entrypoints;
- onboarding, migration, recovery и support bundle API.

## Что нельзя честно считать завершённым без внешней инфраструктуры

- настоящий подписанный Windows `Setup.exe`;
- репутация SmartScreen;
- проверка NSIS на чистых Windows 10 и 11;
- реальный update server и обновление с предыдущей установленной версии;
- NVIDIA/AMD/Intel hardware encoding;
- многочасовой Twitch/Ollama/Whisper soak test;
- финальный юридический аудит сторонних бинарников;
- сертификат издателя и коммерческие документы владельца.

v10.5.1 готова как Release Candidate для Windows build и закрытого теста. Массовая продажа начинается только после прохождения Windows acceptance matrix и подписи артефактов.
