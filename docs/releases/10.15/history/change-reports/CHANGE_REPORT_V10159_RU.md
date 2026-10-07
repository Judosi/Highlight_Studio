# Highlight Studio 10.15.9 — Semantic Quality

10.15.9 — отдельный релиз поверх 10.15.8. Старые ZIP и папки не перезаписываются.
Главная цель версии — вернуть приоритет **лучшим осмысленным моментам из всего VOD**, а не просто набрать целевую длительность.

## 1. Исправлен скрытый Auto/Twitch preset fallback

Раньше `task_preset="balanced"` существовал в defaults, но отсутствовал в таблице `TASK_PRESETS`. В Auto для Twitch такой случай мог откатываться в `irl_funny` / «Только смешное». Это системно повышало ценность смеха, мемов и реакций даже внутри replay/intermission-вставок.

В 10.15.9 добавлен настоящий нейтральный пресет **«Сбалансированный / смысл»**. Auto больше не подменяет обычный Twitch-анализ профилем «Только смешное».

## 2. Semantic Content Guard

Добавлены классы контента:

- `primary_live`;
- `live_reaction`;
- `waiting`;
- `reconnect`;
- `intermission`;
- `replay`;
- `prerecorded`;
- `advertisement`;
- `unknown`.

Высокоуверенные waiting/reconnect/intermission/replay/prerecorded/advertisement-фрагменты не могут попасть в final selection только потому, что внутри есть смешная реплика, донат или динамичный старый клип.

При этом сохранено важное исключение: **настоящая live reaction стримера на внешнее видео остаётся валидным контентом**.

## 3. Защита от ложного OCR veto

Один кадр с технической надписью внутри длинного 3–4-минутного блока не должен уничтожать хороший live-контент. Поэтому на coarse block stage OCR становится жёстким основанием для veto только при повторяемом подтверждении технической сцены и достаточной доле таких samples.

На коротких micro-сценах высокоуверенный технический OCR может быть решающим — там временная локализация уже достаточно точная.

## 4. Весь VOD получает шанс на полноценный AI-анализ

Visual Scan и OCR и раньше могли физически покрывать весь source, но поздний хороший фрагмент мог проиграть до Micro AI, если первоначальный transcript score был ниже ранних top-blocks.

В 10.15.9:

1. ранняя semantic/visual/OCR информация применяется до micro prefilter;
2. Micro source selection берёт глобальных лидеров по quality;
3. дополнительно сохраняет сильные блоки из временных buckets по всему VOD;
4. финальная selection всё равно глобально сравнивает качество и **не обязана** брать одинаковый процент из каждой четверти.

Это temporal fairness, а не искусственная равномерность.

## 5. Micro AI — по смыслу, Qwen3 8B сохранён

Qwen3 8B не заменён на меньшую модель. Prompt теперь оценивает не только «интересность», но и:

- принадлежность к текущему стриму;
- смысловой topic;
- self-contained начало;
- cause -> development -> reaction/payoff;
- replay/reconnect/waiting/intermission;
- необходимость контекста до/после события.

Если модель не возвращает semantic поля, pipeline сохраняет безопасные inherited значения от parent block.

## 6. Storyline теперь проверяет смысл мини-сцены

После отбора storyline-pass анализирует контекст вокруг каждого кандидата. Если несколько секунд до/после делают момент понятным, границы могут быть расширены. Если даже с контекстом фрагмент бессвязный или относится к non-primary content, он удаляется.

Удалённый semantic guard/storyline кандидат не может позднее вернуться через refill.

## 7. Quality-first selection

Целевые 30 минут больше не означают «обязательно заполнить почти 30 минут любым материалом».

Default quality-first thresholds не позволяют использовать слабые/технические кандидаты как filler. Если в двухчасовом VOD реально найдено только 22 минуты сильного материала, корректный результат может быть около 22 минут вместо 30 минут с 8 минутами reconnect/replay.

## 8. Semantic dedup

Dedup и локальный similarity filter учитывают:

- `semantic_topic`;
- `template_signature`;
- content class;
- текст/title/reason.

Одинаковая reconnect/intermission-заставка с разными репликами больше не должна многократно попадать в montage только потому, что текст каждого кандидата различается.

## 9. Temporal diagnostics

`temporal_quality_report.json` сохраняет временное распределение transcript/visual/OCR/candidates/selected и selected seconds. Это позволяет проверить, что конец VOD действительно участвовал в анализе и где именно начинается возможный temporal bias.

`stream_content_guard_*.json` фиксирует content-class decisions и evidence.
`micro_source_selection.json` показывает, какие временные buckets получили Micro AI pass.

## 10. Что сознательно НЕ ухудшалось

- Qwen3 8B сохранён.
- Visual Scan coverage не уменьшен ради скорости.
- OCR coverage не уменьшен ради скорости.
- Temporal fairness не заставляет брать 25% из каждой четверти.
- Quality Guard из 10.15.7 сохранён.
- CPU/GPU Auto, adaptive OCR, lightweight progress polling, corrected ETA и manual Review navigation сохранены.

## 11. Проверка качества

Лучший regression test для 10.15.9 — тот же проблемный 2-часовой VOD, на котором reconnect/replay занимал значительную часть старого montage.

Проверять нужно не только длительность, но и:

- есть ли reconnect/replay в selected segments;
- распределены ли raw/AI candidates по всему VOD;
- есть ли сильные late candidates;
- понятны ли выбранные сцены без просмотра длинного контекста;
- не повторяется ли один technical template;
- получилось ли меньше 30 минут только потому, что слабый filler сознательно не добирался.
