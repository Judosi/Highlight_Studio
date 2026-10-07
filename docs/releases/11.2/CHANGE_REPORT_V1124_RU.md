# Highlight Studio 11.2.4 — Long-form Target Integrity

## Исправленная первопричина

В предоставленном часовом проекте Ollama и Micro AI работали нормально: было обработано 40 из 40 окон. Недобор до 7:23 возник раньше. Крупные блоки обычного live-геймплея получили неподтверждённые классы `prerecorded` и `reconnect`. Затем эти же слова из AI-title/reason повторно попадали в deterministic guard и ошибочно выглядели как второе независимое доказательство.

После coarse-veto Micro-cut видел лишь часть исходника и оставлял короткие речевые всплески. При цели 35 минут связующий gameplay-контекст вообще не мог попасть в монтаж, хотя он относился к текущему эфиру.

## Что изменено

- deterministic content rules анализируют только исходный transcript и OCR, а не сгенерированные AI title/reason;
- обращение к чату, модераторам, таймеру и текущий gameplay используются как независимое live-evidence;
- AI-only non-primary verdict крупного блока без OCR/текстового подтверждения не выполняет четырёхминутный hard-veto до Micro AI;
- явные replay/waiting/reconnect/intermission/advertisement по transcript или повторяемому OCR по-прежнему удаляются;
- при длинной цели Micro AI получает retention-aware инструкцию и не считает связующие части текущей сцены водой;
- после quality, dedup и storyline выполняется bounded context refill: используются только strong `primary_live`/`live_reaction` blocks;
- контекст режется на проверяемые части до 120 секунд, не перекрывает выбранные сцены и всегда виден пользователю;
- `longform_context_refill.json`, `selection_report.json` и `analysis_health.json` показывают точный вклад контекста.

## Результат на данных пользователя

- до: 28 сцен / 443,32 секунды / 21,11% ориентира;
- после: 73 сцены / 2100,00 секунды / 100% ориентира;
- автоматически добавлено: 45 context-сцен / 1656,68 секунды;
- non-primary content в context refill: 0;
- 1 сомнительный prerecorded-блок не использован.

Результат является воспроизводимой переоценкой сохранённых transcript/block/segment данных. Финальный творческий состав после нового реального запуска Micro AI может отличаться, но target и semantic invariants остаются теми же.
