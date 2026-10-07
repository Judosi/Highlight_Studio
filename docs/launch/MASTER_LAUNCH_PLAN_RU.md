# Полный план запуска Highlight Studio

Этот документ разделяет то, что уже автоматизировано в репозитории, и внешние действия владельца продукта.

## 1. Зафиксировать релиз

- создать приватный GitHub-репозиторий;
- загрузить проект;
- использовать ветки `main`, `beta`, `development`;
- выпускать версии только тегами `vX.Y.Z`;
- не добавлять новые функции во время release candidate.

## 2. Заполнить launch config

1. Скопировать `launch/launch_config.example.json` в `launch/launch_config.json`.
2. Заполнить реальные реквизиты, URL, тарифы и публичный ключ.
3. Запустить:

```bash
python tools/launch/configure_launch.py --config launch/launch_config.json
python tools/launch/validate_launch.py --config launch/launch_config.json --strict
```

Секретные ключи в launch config не хранятся.

## 3. Развернуть сайт и license server

- купить домен и VPS;
- настроить DNS;
- заполнить `deploy/.env`;
- запустить `docker compose up -d --build`;
- включить ежедневный backup базы лицензий;
- проверить HTTPS и health endpoint.

## 4. Подключить оплату

- открыть аккаунт у платёжного провайдера;
- направить webhook на `/webhooks/payment`;
- преобразовывать события провайдера в generic-события Highlight Studio;
- проверить успешную оплату, отмену и повторный webhook;
- не хранить данные банковских карт самостоятельно.

## 5. Собрать Windows installer

- добавить GitHub Secrets из `GITHUB_SECRETS_RU.md`;
- запустить Windows build workflow;
- получить NSIS Setup и portable EXE;
- проверить SHA-256 и Authenticode;
- выпустить тег только после acceptance tests.

## 6. Проверить чистую Windows

Минимум 10 конфигураций: без Python/FFmpeg/Ollama, NVIDIA/AMD/Intel, 8/16/32 GB RAM, кириллица в имени пользователя, OneDrive и разные диски.

## 7. Проверить обновление

- установить предыдущую Stable-версию;
- создать проект;
- опубликовать новую версию;
- проверить обновление, backup, сохранность лицензии и проекта;
- проверить Beta → Stable.

## 8. Founders Beta

- 15–20 тестировщиков;
- ручная поддержка;
- 30–50 VOD;
- фиксация устройств, ошибок, скорости и качества;
- никаких массовых рекламных расходов до подтверждения метрик.

## 9. Поддержка и приватность

- реальный email поддержки;
- база знаний;
- SLA ответа;
- support bundle без видео и транскриптов;
- crash reporting только по согласию;
- финальная юридическая проверка документов.

## 10. Массовый выпуск

Запуск разрешён только когда:

```bash
python tools/release/check_mass_release.py --strict
```

возвращает код 0 и нет release blockers.
