# Технический аудит Highlight Studio 11.2.1

## Область проверки

Аудит сфокусирован на цепочке `Whisper → block candidates → Micro AI → selection → dedup → storyline → refill`, которая в 11.1.0 могла вернуть 5–10 минут при ориентире 30–50 минут без явной ошибки выполнения.

## Гарантии релиза

1. Подозрительные длинные Whisper-сегменты с word timestamps разделяются по паузе и максимальному речевому интервалу.
2. Ни одно raw micro window не превышает `micro_max_seconds`.
3. Micro source selection сохраняет сильные блоки и временное покрытие, анализируя до 4× целевой длительности либо весь допустимый пул.
4. `waiting`, `reconnect`, `intermission`, `replay`, `prerecorded` и `advertisement` не возвращаются через quality-recovery refill.
5. Соседние непересекающиеся окна одного parent не считаются дублями.
6. Недобор ниже `target_fill_ratio` получает отдельный outcome `complete_with_quality_shortfall` и снижает readiness score.
7. Старые AI checkpoints используются только при совпадении новых text/window fingerprints.

## Ограничение

Приложение не заполняет ориентир заведомо слабыми сценами. Если после расширенного анализа качественного материала объективно меньше цели, результат остаётся короче, но теперь причина и точные числа отображаются явно.

## Регрессионная проверка

Добавлен набор `tests/test_montage_quality_recovery_1121.py`, покрывающий timing repair, hard window cap, source coverage, continuity guard, bounded refill и shortfall quality report.
