# Технический аудит Highlight Studio 11.2.4

## Гарантии исправления

1. Слово `reconnect` в сгенерированном AI reason не считается deterministic transcript evidence.
2. AI-only coarse verdict не может удалить крупный live-блок без проверки transcript live-interaction, Micro AI или OCR.
3. Явное text/OCR evidence для replay, prerecorded, waiting, reconnect, intermission и advertisement сохраняет приоритет.
4. Long-form context refill запускается только при реальном недоборе после обычного quality/dedup/storyline процесса.
5. В context refill допускаются только `primary_live` и `live_reaction` блоки выше bounded parent-score floor.
6. Добавленные части не пересекают выбранные сцены, ограничены 120 секундами и маркируются как связующий контекст.
7. Target не достигается за счёт semantic non-primary материала: при нехватке безопасного пула сохраняется честный shortfall.
8. Новая Micro AI policy меняет только Micro fingerprints; transcript/Visual/OCR/block checkpoints не инвалидируются глобально.

## Регрессионная проверка

- false `prerecorded` на текущем gameplay → `primary_live` по live-interaction evidence;
- неподтверждённый prerecorded без live-evidence остаётся недоступным для context refill;
- 60 минут источника → 35 минут монтажа без перекрытий и semantic filler;
- недостаточный primary-live pool не добирается replay-блоками;
- сохранены тесты duration capacity 11.2.2 и Micro AI recovery 11.2.3.

## Ограничение

Целевая длительность остаётся недостижимой, если подтверждённого live-материала объективно меньше цели. В таком случае приложение обязано показать недобор, а не использовать replay, заставки или ожидание.
