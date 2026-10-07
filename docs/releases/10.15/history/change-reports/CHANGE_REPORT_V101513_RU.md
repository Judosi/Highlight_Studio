# Highlight Studio 10.15.13 — Phase 1 Remediation

## Зачем этот релиз

10.15.13 — первый поэтапный remediation-релиз после полного технического аудита 10.15.12. Цель этого этапа — сначала закрыть самые опасные P0/P1 и восстановить честную state machine workflow, не затрагивая алгоритм качества нарезки.

## Главное пользовательское исправление: шаг 4 «Монтаж»

Раньше мог возникнуть сценарий:

1. backend уже завершил анализ;
2. Task Center показывает `done` и найденные моменты;
3. основной React snapshot остаётся старым;
4. sidebar продолжает показывать «Монтаж — после анализа».

В 10.15.13 крупные шаги являются ручной навигацией. Пользователь сам выбирает:

`1 Источник → 2 Формат → 3 Анализ → 4 Монтаж → 5 Экспорт`.

Автоматического перехода после завершения анализа нет.

Если пользователь нажимает «Монтаж», а локальный React state всё ещё считает шаг закрытым, frontend перед отказом делает свежий `dashboard-state` запрос. Если backend подтверждает `analysis_current` / `candidates_current`, открывается Review/Монтаж. Анализ с нулём кандидатов тоже считается завершённым и может быть открыт для диагностики.

Заблокированные workflow-кнопки намеренно остаются интерактивными: клик либо повторно проверяет authoritative backend state, либо объясняет недостающий prerequisite.

## Закрытые findings Phase 1

### HS-AUDIT-001 — P0 — canonical project identity / RBAC

Project ID в web API теперь должен быть каноническим. Encoded aliases с `%` больше не проходят как альтернативный путь к тому же project ID. Это закрывает расхождение между raw-path authorization и последующим multi-decode filesystem resolution.

### HS-AUDIT-002 — P1 — concurrent segment writes

Revision check и запись `segments.json` выполняются под одним per-project mutation lock. Два запроса с одной и той же revision больше не могут оба успешно пройти проверку и молча потерять одну правку: второй получает stale revision conflict.

### HS-AUDIT-003 — P1 — project.json lost update

Сохранение settings и финализация Twitch source используют общий per-project metadata lock для полного read-modify-write. Это предотвращает потерю source metadata либо новых settings при конкурентной записи.

### HS-AUDIT-004 — P1 — source identity

Source fingerprint усилен:

- файлы до 128 MiB хэшируются полностью;
- большие файлы получают 64 равномерно распределённых sample-блока вместо только first/middle/last;
- в сигнатуру добавлены `ctime_ns` и `signature_version`.

Точный audit-repro с внутренним изменением файла при сохранении размера/mtime теперь обнаруживается.

### HS-AUDIT-005 — P1 — Add Candidate project-switch race

Source React path захватывает project generation/scope до async request и повторно проверяет его после ответа. Ответ Project A не должен мутировать UI уже открытого Project B.

### HS-AUDIT-016 — P1 — job start vs delete/clear-cache TOCTOU

Start background job, delete project и clear project cache координируются через общий per-project lifecycle lock. Деструктивная операция не должна проскочить между проверкой jobs и реальным стартом worker.

### HS-AUDIT-021 — P1 — cancel_requested после restart

`cancel_requested` включён в startup recovery и переводится в логически честное interrupted/recoverable состояние вместо вечной активной задачи без процесса.

## Release / test reliability

`tests/conftest.py` теперь добавляет `backend/src` в `sys.path`, поэтому стандартная команда `python -m pytest -q` больше не должна падать на collection с `ModuleNotFoundError: highlight_studio` только из-за release layout.

## Что НЕ менялось

- Qwen3 8B;
- Semantic Quality;
- scoring thresholds;
- reconnect/replay guard;
- temporal fairness;
- Visual/OCR coverage;
- selection/refill quality policy;
- target-duration quality-first semantics.

## Известный незакрытый blocker

**HS-AUDIT-008 остаётся открытым.** Production UI всё ещё состоит из React bundle + `ux-workflow-101513.js` compatibility layer. В текущем Linux QA environment невозможно выполнить clean Vite rebuild из-за отсутствующих npm package payloads и недоступного npm registry. Runtime-поведение Phase 1 внесено и в source, и в production bundle/compatibility layer и покрыто browser regression, но архитектурно source/dist divergence будет убрана отдельным build-cleanup этапом.

Поэтому 10.15.13 — тестовый remediation-релиз, а не разрешение на массовый/платный выпуск.
