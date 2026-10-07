# Frontend Audit — Highlight Studio 10.15.3

Дата аудита: 2026-08-15

## Итог

Проверен весь доступный frontend-релиз: 60 файлов в `frontend/`, исходный React, production bundle, HTML, все CSS-слои, frontend tests и standalone YouTube Publisher.

Это не означает, что каждая строка вручную переписана. Проверка состояла из двух частей:

1. программный разбор каждого frontend-файла и поиск структурных/синтаксических/каскадных проблем;
2. ручной разбор критических пользовательских цепочек и подтверждение production bundle в Chromium.

## Размер и технический долг

`frontend/src/app/App.jsx` остаётся крупным legacy-компонентом:

- 3191 строка;
- 115 `useState`;
- 23 `useEffect`;
- 56 вызовов `apiFetch`;
- 122 функции, из них 62 async.

Это работает, но увеличивает риск скрытых связей между проектом, workflow, задачами и UI. Полная декомпозиция на отдельные feature controllers/components рекомендована отдельным релизом, а не одновременно со стабилизацией production UI.

`studio-final.css` содержит 2074 строки. Большое количество `!important` остаётся следствием старой CSS-архитектуры. В production 10.15.3 риск каскадных конфликтов снижен тем, что после базовых asset styles и v3 подключается только один release-specific final owner stylesheet.

## Подтверждённые дефекты

### 1. Startup workflow race

Сохранённый `activeStep` мог валидироваться, когда `project` ещё был `null`. В результате сохранённый Format/Analysis/Review/Export сбрасывался на Projects до завершения `initApp()`.

Исправление: workflow guard ничего не меняет, пока `appBooting === true`.

### 2. New Project navigation conflict

До существования project workflow guard должен разрешать `import`. Иначе `beginProject()` открывает Import, а следующий effect мгновенно возвращает Projects.

Исправление подтверждено в source и production bundle.

### 3. Stale project dashboard race

Запрос dashboard предыдущего project мог завершиться после переключения на другой project.

Исправление: перед применением dashboard response сверяется `highlightStudioLastProject`; stale response отбрасывается.

### 4. Stale browser bundle

FastAPI отдаёт статические assets с `Cache-Control: immutable` на год. Если изменить содержимое bundle, но оставить прежнее имя `index-*.js`, браузер имеет право использовать старую копию.

Это способно объяснить часть случаев, когда в новой папке визуально открывался старый UI.

Исправление: production assets 10.15.3 имеют новые release-specific имена:

- `assets/index-10153-*.js`;
- `assets/index-10153-*.css`;
- `studio-final-10153.css`.

Release verifier не пропустит сборку без release-specific asset names.

### 5. CSS patch layering

Раньше production HTML отдельно подключал v4, v5, v6 и v7. Каждый следующий файл исправлял предыдущий, поэтому новые media queries могли снова ломать sidebar/task center/workflow.

В 10.15.3 production HTML больше не подключает эти четыре файла отдельно. Они сведены в один final owner stylesheet.

### 6. Long Twitch/project names

Очень длинное имя могло увеличить min-content width и вытолкнуть элементы Source context/topbar за viewport.

Добавлены `min-width: 0`, wrapping и ellipsis в критических контейнерах. Отдельный Chromium scenario использует заведомо чрезмерно длинное имя проекта.

### 7. Task Center geometry

Task Center не должен быть частью normal flow. Он закрыт по умолчанию даже при активной задаче и появляется только после ручного нажатия.

Drawer имеет fixed positioning и внутренний `scrollWidth === clientWidth` в browser test.

### 8. Twitch download workflow

Если VOD скачивается и `source_video_path` ещё пуст, пользователь должен оставаться на Source.

Chromium test специально стартует с сохранённым `style`, активной Twitch-задачей и незавершённым source. Фактический результат: Source остаётся активным, Task Center автоматически не открывается.

## Статические проверки

### HTML

Проверено 4 HTML-файла через parser:

- parse errors: 0;
- duplicate `id`: 0;
- `<img>` без `alt`: 0;
- HTML `_blank` links без защиты: 0.

### CSS

Проверено 13 CSS-файлов через `tinycss2`:

- parse errors: 0.

### JavaScript

Все plain `.js` files в `src/public/dist/tests` проходят `node --check`.

Production bundle также проходит `node --check`.

Файловые/export окна в React production bundle используют `noopener,noreferrer`.

## Реальный browser-layout audit

Используется production JS bundle и production CSS, а не JSX-макет.

Viewports:

- 2560×1440;
- 1920×1080;
- 1600×900;
- 1536×864 — приблизительный CSS viewport для 1920×1080 при Windows scale 125%;
- 1440×900;
- 1366×768;
- 1280×720 — также полезный сценарий повышенного Windows scale;
- 1024×768.

Состояния:

- Projects без проекта;
- Import без проекта;
- Twitch VOD downloading;
- Format;
- Analysis busy;
- Review;
- Export.

Итого: **56 layout cases**.

Результат:

- document horizontal overflow: 0 failures;
- body horizontal overflow: 0 failures;
- visible elements outside viewport: 0 failures;
- visible unnamed controls в проверяемых workflow states: 0 failures.

Отдельные interaction assertions:

- compact sidebar: **82 px**;
- New Project → Source: **успешно**;
- Twitch download при незавершённом source остаётся на Source: **успешно**;
- Task Center автоматически при busy: **не открыт**;
- Task Center после click: **открыт**;
- Task Center horizontal scroll: `clientWidth == scrollWidth`.

Скрипт сохранён в `tools/diagnostics/audit_frontend_layout.py`.

## Tests

Frontend tests, не требующие отсутствующего в сборочном окружении npm-пакета `jsdom`, проходят. `app.integration.test.js` и `onboarding.test.js` не запускались именно по этой причине — это отдельно отмечается, а не считается passed.

Python suite:

- 192 passed;
- 2 failed — обе проверки требуют рабочий Whisper Silero VAD runtime, отсутствующий в текущем тестовом окружении.

Эти два падения не относятся к React/CSS, но сборка не маркируется как «все тесты зелёные».

## Что нельзя доказать в контейнере

Следующие вещи требуют дополнительного smoke test на реальном Windows ПК пользователя:

- Windows native file picker/desktop bridge;
- реальное системное масштабирование Windows и конкретный браузер пользователя;
- настоящий многогигабайтный Twitch VOD на протяжении всей загрузки;
- локальный FFmpeg/GPU/Whisper/Ollama runtime пользователя;
- реальный OAuth YouTube channel flow.

## Рекомендованный следующий архитектурный этап

После стабилизации 10.15.3 имеет смысл отдельным релизом разбить `App.jsx` минимум на:

- `ProjectController`;
- `WorkflowController`;
- `TaskController`;
- `SourceStep`;
- `FormatStep`;
- `AnalysisStep`;
- `ReviewStep`;
- `ExportStep`;
- `YouTubeController`.

И отдельно постепенно удалить старые CSS поколения вместо бесконечного расширения override-слоя. Делать это в том же hotfix-релизе рискованно: такой рефакторинг должен иметь отдельный regression cycle.
