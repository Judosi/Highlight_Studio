# Highlight Studio License Service

Reference service for activation, refresh, device deactivation and generic payment webhooks. Deploy only behind HTTPS and a reverse proxy with rate limiting.

Required environment variables:

- `HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64`
- `HIGHLIGHT_STUDIO_LICENSE_ADMIN_TOKEN`
- `HIGHLIGHT_STUDIO_PAYMENT_WEBHOOK_SECRET`
- `HIGHLIGHT_STUDIO_LICENSE_DB`

The service intentionally does not include a payment-provider-specific checkout implementation. A provider webhook should be translated into the documented signed generic event.
