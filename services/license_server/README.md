# Highlight Studio License Service

Reference service for activation, refresh, device deactivation and generic payment webhooks. Deploy only behind HTTPS and a reverse proxy with rate limiting.

Required environment variables:

- `HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64`
- `HIGHLIGHT_STUDIO_LICENSE_ADMIN_TOKEN`
- `HIGHLIGHT_STUDIO_PAYMENT_WEBHOOK_SECRET`
- `HIGHLIGHT_STUDIO_LICENSE_DB`

Optional environment variables:

- `HIGHLIGHT_STUDIO_TRIAL_DAYS` (default: `14`, allowed range: 1–90)

When a desktop build has `licenseServerUrl` configured, its first trial start
contacts `POST /trial/start`. The service stores one immutable trial window per
persisted device ID and returns an Ed25519-signed entitlement. Repeating the
request, deleting the local state, or reinstalling the app returns the original
expiration rather than creating a new trial. After that first request the signed
entitlement can be verified offline until its original expiration.

The service intentionally does not include a payment-provider-specific checkout implementation. A provider webhook should be translated into the documented signed generic event.
