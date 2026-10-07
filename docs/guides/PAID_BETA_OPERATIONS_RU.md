# Эксплуатация Highlight Studio v10.6.0 Paid Beta

## Переменные release-сборки

```text
HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64  публичный Ed25519-ключ
HIGHLIGHT_STUDIO_LICENSE_SERVER_URL      HTTPS API активации, без завершающего /api
HIGHLIGHT_STUDIO_CHECKOUT_URL            HTTPS-страница оплаты
HIGHLIGHT_STUDIO_ACCOUNT_URL             HTTPS-личный кабинет
HIGHLIGHT_STUDIO_SUPPORT_URL             HTTPS-форма поддержки
HIGHLIGHT_STUDIO_PRIVACY_URL             опубликованная политика
HIGHLIGHT_STUDIO_TERMS_URL               опубликованные условия
HIGHLIGHT_STUDIO_TELEMETRY_URL           необязательный HTTPS endpoint событий
HIGHLIGHT_STUDIO_FEEDBACK_URL            необязательный HTTPS endpoint beta-отзывов
HIGHLIGHT_STUDIO_TRIAL_DAYS               14 по умолчанию
```

## Контракт license server

`POST /activate`

```json
{"license_key":"XXXX-XXXX","device_id":"sha256","app_version":"v10.6.0-paid-beta"}
```

Ответ:

```json
{"token":"HSB1.<payload>.<ed25519-signature>"}
```

`POST /refresh` принимает текущий token, device_id и app_version и возвращает новый token. Приватный Ed25519-ключ никогда не включается в desktop-приложение.

Для первых Founders Beta можно выдавать офлайн-токены через `tools/licensing/sign_license.py`. Перед массовой продажей активацию нужно связать с webhook платёжного провайдера и собственной базой лицензий.

## Метрики качества

Экран Paid Beta показывает только локальные агрегаты: количество проектов, готовых рендеров, кандидатов и финальных сегментов. Содержимое моментов не входит в метрики.

## Ограничения beta-лицензирования

Локальный trial является удобным beta-механизмом, а не неуязвимой DRM. До массовой продажи сервер должен учитывать активации и места устройств. Удаление локальных служебных файлов технически может сбросить локальное состояние, поэтому оплату и entitlement нельзя считать защищёнными только desktop-кодом. Trial начинается после завершённого legal onboarding, а не при пассивном открытии приложения.
