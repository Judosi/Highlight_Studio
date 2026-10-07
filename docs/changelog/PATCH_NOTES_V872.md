# Patch notes v8.7.2 — IRL Sync Fix

Исправлен UX/логический рассинхрон IRL-настроек.

## Что изменено

- Если пользователь выбирает `A/B режим: IRL ...`, а `Content type` оставлен `Auto`, backend автоматически включает `content_type = IRL стрим`.
- Frontend теперь синхронизирует IRL A/B режим с `Content type`.
- `Content type` продублирован в основной карточке настроек, чтобы IRL был виден не только в блоке быстрых режимов.
- `is_irl_settings`, IRL prompt, IRL scoring bonuses и Adaptive Auto теперь учитывают IRL A/B режим даже при старых проектах с `content_type = Auto`.
- Добавлены smoke-тесты на IRL sync.

## Почему это важно

В v8.7.1 можно было выбрать `IRL плотный/история/хаос`, но оставить `content_type = Auto`. В этом случае часть IRL-логики работала, а часть — нет. В v8.7.2 IRL применяется целиком.
