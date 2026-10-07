# Архитектура Highlight Studio 11.2.7

## Принцип

Корень релиза — это **точка входа**, а не склад служебных файлов. Пользователь видит один основной `START_HERE.bat`; всё дополнительное разложено по назначению. При этом проверенные runtime-пути 10.15.17 (`backend/`, `frontend/`, `scripts/`, `vendor/`) не перемещались, чтобы архитектурная уборка не создала функциональный риск.

## Дерево

```text
Highlight_Studio_11.2.7/
├── START_HERE.bat                 # единственная основная точка запуска
├── README.md
├── EXTRACT_BEFORE_STARTING.txt
├── release_identity.json
├── pyproject.toml
├── alembic.ini
├── commands/                      # ручные пользовательские команды
│   ├── launch/
│   │   ├── ADVANCED_START_OPTIONS.bat
│   │   ├── START_DESKTOP.bat
│   │   ├── START_WEB_LOCAL.bat
│   │   ├── STOP_WEB_LOCAL.bat
│   │   ├── START_YOUTUBE_READY.bat
│   │   └── OPEN_YOUTUBE_PUBLISHER.bat
│   ├── setup/
│   │   ├── INSTALL_FFMPEG.bat
│   │   └── INSTALL_GPU_ACCELERATION.bat
│   └── diagnostics/
│       └── VERIFY_RUNNING_VERSION.bat
├── backend/
│   ├── requirements*.txt
│   ├── *.py                       # compatibility imports
│   └── src/highlight_studio/
│       ├── api/                   # HTTP routes/orchestration + schemas.py contracts
│       ├── core/                  # config + shared primitives
│       ├── services/              # analysis/render logic + ai_policy/media_models
│       ├── integrations/          # Ollama/Twitch/YouTube adapters
│       └── infrastructure/        # jobs/auth/db/licensing
├── frontend/
│   ├── src/
│   ├── public/
│   ├── tests/
│   └── dist/                      # production build
├── desktop/                       # Electron/Tauri shells
├── services/                      # independent services
├── website/                       # public website
├── scripts/windows/               # internal launcher/build scripts
├── tools/
│   ├── diagnostics/
│   ├── desktop/
│   ├── quality/                  # architecture_audit.py + quality tooling
│   ├── licensing/
│   ├── launch/
│   └── release/
│       ├── release_layout.py      # canonical release contract
│       └── checks/
├── docs/
│   ├── guides/
│   ├── audits/
│   ├── releases/10.15/
│   │   ├── history/
│   │   └── remediation/
│   ├── changelog/
│   ├── legal/
│   ├── launch/
│   └── templates/
├── tests/
├── vendor/
├── deploy/
├── alembic/
└── .github/
```

## Правила зависимостей backend

- `api` может вызывать `services`, `integrations`, `infrastructure`, `core`.
- `services` не импортирует FastAPI и содержит основную медиалогику.
- `integrations` инкапсулирует внешние программы/сервисы.
- `infrastructure` отвечает за persistence, auth, licensing и release/runtime механизмы.
- `core` не зависит от HTTP-слоя.

## Надёжность 11.2

- `core/durable_pipeline.py` — атомарное состояние стадий и recovery после interrupted shutdown.
- `infrastructure/resource_manager.py` — общий реентерабельный лимит RAM/VRAM-тяжёлых операций между проектами без self-deadlock.
- `core/artifacts.py` — portable resolver исходника, защита от выхода относительного пути за проект и content-based signature.
- AI runtime отделяет TTFT от stream stall и защищён circuit breaker по provider/model.
- Shorts export использует fingerprinted pending-файлы, ffprobe validation и atomic replace; входные правки одного Short допускаются к записи вместе с его job.

## Правила файловой структуры

1. Новые пользовательские BAT не добавляются в корень — только в подходящий раздел `commands/`.
2. Новый исторический отчёт не добавляется в корень — только в `docs/releases/<ветка>/`.
3. Скриншоты аудита идут в `docs/audits/.../screenshots/`.
4. Release-проверки идут в `tools/release/checks/`.
5. Runtime-каталоги `.highlight_studio/`, `.venv/`, `projects/`, logs/cache/media не входят в ZIP.
6. Список обязательных файлов релиза меняется только в `tools/release/release_layout.py`.

## Runtime-данные

Пользовательские данные не хранятся в исходниках. На Windows используются writable-пути `%LOCALAPPDATA%\HighlightStudio\` и `%USERPROFILE%\Videos\Highlight Studio\`; portable/source режим может использовать `.highlight_studio/` и `projects/` рядом с приложением.

## Следующий этап рефакторинга

Крупные Python-модули (`api/app.py`, затем `services/pipeline.py`) можно делить отдельно, по функциональным route/service-модулям и только с contract/regression-тестами. В 11.2.7 намеренно не смешивалась файловая уборка с таким рискованным функциональным рефакторингом.


## Уточнение фактического состояния 11.2.7

Подробная карта зависимостей и остаточные риски: [технический аудит](releases/11.2/TECHNICAL_AUDIT_11.2.7_RU.md). Единственный desktop runtime — Electron; Tauri experimental. Root backend/*.py — aliases, не вторые реализации. Production owner UI — React; bridge shorts-editor-1126.js хранится, но не подключается. Layers CSS сохранены до visual characterization. Source/dist проверяются build-manifest.json. Project locks/ResourceManager process-local: запуск нескольких backend workers на одной data directory не заявлен поддерживаемым.
## Безопасная декомпозиция 11.2.7 (architecture-safe patch)

Чтобы улучшить структуру без риска переписать рабочий media pipeline, выполнена только декомпозиция участков с низкой связанностью:

- `api/schemas.py` теперь владеет Pydantic-контрактами API и настройками; `api/app.py` сохраняет прежние имена через импорт, поэтому старые импорты совместимы.
- `services/media_models.py` владеет `Candidate` и `TranscriptSegment`; `services/pipeline.py` продолжает их реэкспортировать.
- `services/ai_policy.py` содержит чистые правила AI batching/completeness/retry без файлового I/O и FFmpeg.
- временный frontend test fixture больше не хранится в корне `frontend/`: тест создаёт его рядом с тестами и удаляет после выполнения.
- `tools/quality/architecture_audit.py` автоматически проверяет направление зависимостей слоёв, чистоту корня и форму compatibility wrappers.

Крупные владельцы состояния (`api/app.py`, `services/pipeline.py`, `frontend/src/app/App.jsx`) **не разбивались механически**: их дальнейшее деление требует отдельной characterization/visual/media фазы. Архитектурный аудит помечает их warning, а не маскирует риск массового рефакторинга.

