# Highlight Studio v10.3.0 — Release Hardening

Дата подготовки: 11 июля 2026 года.

## Цель версии

Версия 10.3.0 не добавляет декоративные функции. Она закрывает подтверждённые runtime-ошибки из релизного аудита, усиливает безопасность и делает сборку честнее и воспроизводимее.

## Исправленные блокирующие ошибки

### Twitch

- Исправлены два API-маршрута, которые падали с `NameError` из-за отсутствующей функции `merged_settings`:
  - `GET /api/projects/{id}/twitch-download-plan`;
  - `POST /api/projects/{id}/twitch-speed-test`.
- Добавлены регрессионные тесты для обоих сценариев.

### Аппаратное кодирование

- Добавлен отсутствовавший импорт `subprocess`.
- Проверка FFmpeg encoder теперь разбирает реальные токены списка кодировщиков и кэширует результат.
- Добавлен фактический fallback: если NVENC/QSV/AMF указан FFmpeg, но не запускается из-за драйвера или отсутствующего GPU, текущая часть автоматически повторяется через `libx264`.
- Ошибка аппаратного encoder больше не должна уничтожать весь многочасовой рендер.

### SQLite

- Соединения job store теперь гарантированно закрываются через `contextlib.closing`.
- Устранены предупреждения `ResourceWarning: unclosed database` в тестовых сценариях.

### Список результатов

- Убраны дубликаты.
- Из пользовательского списка исключены `render_parts`, `concat.txt`, внутренние JSON, кэши и другие служебные файлы.
- Результаты теперь содержат понятный тип и размер файла.

## Безопасность и диагностика

- Добавлены Content Security Policy и базовые защитные HTTP-заголовки.
- Публичный ответ HTTP 500 больше не раскрывает traceback.
- Пользователь получает `diagnostic_id`, а полный traceback сохраняется локально.
- Добавлен ротируемый backend-лог: до пяти файлов по 5 MB.
- Runtime-данные по умолчанию вынесены из каталога программы:
  - Windows data: `%LOCALAPPDATA%\HighlightStudio`;
  - проекты: `%USERPROFILE%\Videos\Highlight Studio`.
- Сохранены переменные окружения для portable и custom-path режима.
- При завершении backend активные задачи получают запрос на отмену, а зарегистрированные дочерние процессы завершаются.

## Desktop shell

- Electron теперь сам запускает локальный backend, ждёт `/api/health`, и только после готовности открывает окно.
- При завершении приложения Electron закрывает только тот backend, который запустил сам.
- Добавлены single-instance guard, sandbox и ограничения навигации.
- Electron закреплён на версии `43.1.0`; добавлен lock-файл и требование Node.js 22 для разработки.
- Production-пользователю Node.js после сборки не требуется.

## Frontend

- Добавлен React Error Boundary с понятным экраном критической ошибки.
- Добавлен ESLint.
- Добавлены первые frontend unit-тесты.
- Синхронизированы версии UI, HTML title, package metadata и backend.
- Внутренний `Release Audit 100/100` переименован в честный `Packaging Audit` и снабжён ограничениями.

## Запуск и совместимость

- Поддерживаемый Python ограничен диапазоном 3.10–3.13.
- Browser launcher больше не открывает страницу раньше готовности backend.
- `pytest` вынесен из production requirements в `requirements-dev.txt`.
- Добавлен CI для Ubuntu и Windows, Python 3.11/3.12, frontend и Electron shell.
- В CI добавлены dependency audit, lint, tests, build и проверка release ZIP.

## Чистая release-сборка

- Добавлен SHA-256 manifest для каждого файла внутри ZIP.
- Создаётся отдельный `.sha256` для всего архива.
- Проверяющий скрипт пересчитывает каждый hash и отклоняет изменённый архив.
- Из ZIP исключаются:
  - токены;
  - SQLite runtime database;
  - проекты и пользовательские видео;
  - логи;
  - `.venv`;
  - `node_modules`;
  - `.pytest_cache`, `.ruff_cache`, `.mypy_cache`;
  - coverage-отчёты;
  - временные аудио- и render-файлы.

## Проверки версии

- Python compile — успешно.
- Ruff — без замечаний.
- Backend tests — 73 passed.
- Реальный FFmpeg integration test — успешно создаёт MP4 с видео и аудио.
- Frontend ESLint — успешно.
- Frontend tests — 3 passed.
- Vite production build — успешно.
- Frontend npm audit — 0 найденных уязвимостей.
- Electron npm audit — 0 найденных уязвимостей.
- Clean ZIP verification — успешно.
- Clean extraction + backend startup + `/api/health` + frontend HTTP 200 — успешно.

## Что версия не может честно гарантировать

Эта версия закрывает найденные кодовые ошибки, но не заменяет продуктовую валидацию. До массового платного релиза остаются:

- подписанный Windows installer и uninstaller;
- standalone backend sidecar с встроенным Python;
- встроенный FFmpeg/FFprobe в installer;
- auto-update и rollback;
- тесты на чистых Windows 10 и Windows 11;
- реальные многочасовые Twitch VOD;
- полный сценарий Twitch → Whisper → Ollama → Review → Render;
- матрица NVIDIA/AMD/Intel GPU и разных драйверов;
- benchmark качества AI на размеченных стримах;
- дальнейшее увеличение покрытия `pipeline`, Twitch и Ollama;
- дальнейшее разделение крупных файлов `app.py`, `pipeline.py` и `App.jsx`.

Поэтому v10.3.0 следует считать значительно укреплённой закрытой beta-сборкой, но не окончательным массовым коммерческим релизом.
