# Highlight Studio 10.15.14 — AI Reliability & Phase 2 Remediation

## Почему появился этот релиз

Пользовательский runtime-архив 10.15.12 доказал критическую проблему AI reliability: Qwen3:8b несколько раз возвращал валидный JSON вида `{"error": ...}` либо объект с numeric keys, но pipeline писал `AI batch N/12 готов`. Только `batch_0001.json` был реально сохранён. На batch 8 запрос завис без terminal trace event.

10.15.14 исправляет именно эту цепочку, не меняя Qwen3:8b и не снижая объём/качество анализа.

## AI-001 — false-success batch

Теперь JSON синтаксически валиден недостаточно. Central AI Runtime проверяет operation contract:

- explicit `error` payload → failure;
- для Block AI обязателен `blocks` collection;
- для каждого expected ID обязателен item;
- обязательные поля `id` и `score` валидируются;
- numeric-object `{ "1": 8.5, ... }` исправляется локально в `{ "blocks": [...] }` без нового Qwen inference.

Batch получает `done` и checkpoint только после полного валидного результата.

## AI-002 — hang/retry budget

Старый путь допускал комбинацию большого timeout и нескольких уровней retry. Теперь:

- Block/Micro outer full-batch semantic retry = 1;
- transient transport retry принадлежит `AIExecutionController` и ограничен 1–3 попытками;
- default transport budget = 2;
- stream stall timeout default = 45s;
- hard wall-clock deadline default = 360s;
- hard deadline считается non-transient: тот же огромный prompt не отправляется повторно;
- во время живого streaming response status получает heartbeat `Ollama работает ...`.

## AI-003 — recovery без потери качества

Если часть ID пропущена:

1. сохраняются уже валидные items;
2. запрашиваются только missing IDs;
3. если multi-ID repair не помог — single-ID rescue;
4. если хотя бы один expected ID всё равно отсутствует, stage становится failed/recoverable;
5. уже валидные checkpoint batches остаются и повторный запуск может продолжить без повторной оценки готовых batches.

Нельзя молча считать partial AI coverage полноценным анализом.

## AI-004 — context-safe batching

Batch planner измеряет фактическую длину prompt и уменьшает число items в одном request, если context budget превышен. Исходные blocks/windows не обрезаются и не выбрасываются; меняется только grouping запросов.

## Hardware runtime

Auto Hardware теперь рассматривает сохранённые CPU/int8/libx264 значения старых Auto-профилей как legacy implementation residue, а не как ручной выбор. Если CTranslate2 реально видит CUDA, Auto рекомендует и применяет `cuda` + совместимый compute type. Manual override сохраняется.

## Дополнительный remediation из аудита

Исправлены/усилены:

- auth account-enumeration через `email_sent`;
- rate limiting recovery/verification;
- bounded active recovery tokens без мгновенной инвалидизации предыдущей ссылки;
- server-browser-cookie boundary в web Twitch;
- viewer generic file permissions;
- legacy segment mutation locking;
- clear-cache false success;
- recoverable web project deletion order;
- canonical render diagnostic path;
- source React stale guards для access/hardware/task preset;
- новый project получает только user-facing intent settings, а не скрытый prompt/thresholds прошлого проекта;
- compatibility Task Center больше не рендерит второй список jobs и не создаёт второй 5-second UI poller.

## Что намеренно НЕ менялось

- Qwen3:8b;
- Semantic Quality scoring;
- reconnect/replay semantic guard;
- temporal fairness;
- Visual/OCR coverage;
- quality-first target duration policy.

## Незакрытая архитектурная проблема

HS-AUDIT-008/010 остаются открыты: clean Vite source→dist rebuild не подтверждён в текущей QA среде. Production UI всё ещё использует исторический React bundle и `ux-workflow-101513.js` compatibility layer. В 10.15.14 второй jobs renderer из compatibility layer отключён, но полностью удалить layer можно только после воспроизводимого clean frontend build.
