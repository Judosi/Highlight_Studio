# Highlight Studio 10.15.17 — Post-process Reliability

## Основание

В пользовательском логе 10.15.16 основной анализ и рендер завершились успешно: 126 микро-кандидатов, 50 финальных сегментов и готовый `highlight_final.mp4`. При этом были подтверждены три остаточные проблемы постобработки:

1. `Dedup AI skipped: Ollama stream stalled for 45s without data`.
2. `Storyline pass skipped: ... Invalid request: The provided text is not a valid JSON object.`
3. После refill до ~47:32 локальный duplicate filter снова сокращал монтаж до ~38:47, после чего повторный refill проходил по той же схеме и оставлял shortfall ~672 s.

## Что изменено

### Dedup AI

- Перед постобработкой добавлен `warmup()` Qwen. Это отделяет загрузку модели после `unload()` от stream-stall watchdog.
- Старый объёмный prompt заменён компактным глобальным представлением: ID, score, content class, topic, template, title и короткий text preview.
- Ответ сокращён до `drop_ids/groups`, чтобы уменьшить generation latency.
- Если глобальный запрос падает, выполняются небольшие перекрывающиеся rescue-batches.
- AI-дубли помечаются `decision=remove`, поэтому refill не может случайно вернуть их обратно.
- После AI остаётся deterministic local duplicate guard.

### Storyline

- До 80 контекстно-насыщенных сцен больше не отправляются одним запросом.
- По умолчанию Storyline обрабатывает небольшие batches и использует contract validation `segments/id/keep`.
- Частичный или ошибочный batch не повторяет весь этап: rescue выполняется только для missing/failed IDs малыми batches.
- В non-strict режиме неразобранный хороший кандидат сохраняется, а high-confidence non-primary сцена всё равно удаляется deterministic guard.
- В проект пишется `storyline_runtime.json` с requested/resolved/unresolved и счётчиками primary/rescue batches.

### Добор длительности

- Исправлен порядок: local duplicate filter запускается до refill, а не после него.
- Каждый добавляемый кандидат проверяется на overlap, semantic quality и duplicate similarity до вставки.
- Первый pass строгий; второй допускает только высококачественную отличающуюся сцену из той же широкой темы, но exact TEMPLATE повторы остаются запрещены.
- Refill старается дойти до `target_minutes`. `target_fill_ratio` теперь служит нижней границей качества для диагностики shortfall, а не причиной остановиться раньше цели при наличии подходящих сцен.

## Проверки

Добавлен `tests/test_postprocess_reliability_101517.py`, который воспроизводит:

- старую ошибку refill/filter/refill;
- прекращение добора на 94% вместо target;
- Storyline error payload с восстановлением через rescue batches;
- Dedup stream stall с восстановлением через rescue batches и запретом повторного добавления AI-дубля.

Также повторно запускаются regression-наборы 10.15.4, 10.15.9, 10.15.12 и 10.15.16.
