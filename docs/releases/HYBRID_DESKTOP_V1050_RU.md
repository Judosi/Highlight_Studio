# Highlight Studio v10.5.0 — Hybrid Desktop

## Что означает «гибрид»

Интерфейс остаётся React-приложением, а тяжёлая работа выполняется локальным
Python-движком. Electron запускает движок как sidecar, ждёт `/api/health`, затем
открывает интерфейс. Пользователь не должен видеть терминал, `uvicorn`, `pip` или
виртуальное окружение.

## Компоненты релиза

```text
Highlight Studio.exe                  Electron UI
resources/engine/                     standalone Python engine
resources/app/frontend/dist/          React production build
resources/app/vendor/ffmpeg/           FFmpeg + FFprobe
resources/app/vendor/aria2/            aria2
resources/app/vendor/twitchdownloadercli/ TwitchDownloaderCLI
```

Пользовательские файлы не записываются в Program Files:

```text
%LOCALAPPDATA%\HighlightStudio         база задач, токен, журналы, настройки
%USERPROFILE%\Videos\Highlight Studio проекты и готовые ролики
```

## Что добавлено

- standalone entrypoint `backend/desktop_entry.py`;
- PyInstaller one-folder spec;
- Electron preload с изолированным IPC;
- системный выбор видео;
- нативное открытие готового ролика и папки проекта;
- динамический локальный порт при конфликте 8000;
- строгая проверка origin для IPC;
- запрет разрешений камеры, микрофона и геолокации;
- electron-builder конфигурация для NSIS и portable EXE;
- автоматическая подготовка FFmpeg;
- Windows build script и GitHub Actions workflow;
- разделение install resources и runtime data;
- same-origin API для любого выбранного локального порта.

## Сборка на Windows

Требуются Python 3.10–3.13, Node.js 22 и интернет во время сборки.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/build_hybrid_release.ps1
```

Результаты появятся в `build/desktop/installer/`.

## Что не включено автоматически

- Ollama и модели: приложение проверяет их при первом запуске;
- цифровой сертификат: передаётся через `CSC_LINK` и `CSC_KEY_PASSWORD`;
- автоматическое обновление: запланировано после стабилизации installer build;
- лицензионные и коммерческие документы владельца продукта.

## Честный статус

Исходный гибридный проект и цепочка сборки готовы к Windows build. Сам Windows
installer должен быть собран и проверен на чистой Windows-машине или через
workflow `Build Windows Hybrid Desktop`. Linux-среда не может честно подтвердить
SmartScreen, NVENC/AMF/QSV и поведение NSIS на реальном пользовательском ПК.
