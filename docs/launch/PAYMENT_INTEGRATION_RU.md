# Подключение оплаты

Desktop-приложение не должно получать данные карты. Покупатель открывает HTTPS checkout в браузере, а провайдер отправляет подписанный webhook на сервер.

Generic payload для `POST /webhooks/payment`:

```json
{
  "event": "subscription.activated",
  "customer_id": "customer-123",
  "license_key": "",
  "plan": "creator",
  "days": 30,
  "max_devices": 2,
  "entitlements": ["analysis", "twitch", "render", "shorts", "metadata"]
}
```

Заголовок `X-HS-Signature` — HMAC-SHA256 от raw body с `HIGHLIGHT_STUDIO_PAYMENT_WEBHOOK_SECRET`.

Для отмены используется `subscription.cancelled` и `license_key`. Перед production необходимо добавить idempotency mapping для конкретного провайдера и протестировать повторную доставку webhook.
