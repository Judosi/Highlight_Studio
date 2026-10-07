# Домен и DNS

Рекомендуемая схема:

- `highlightstudio.example` — сайт;
- `api.highlightstudio.example` — license API;
- `updates.highlightstudio.example/stable` — Stable feed;
- `updates.highlightstudio.example/beta` — Beta feed;
- `download.highlightstudio.example` — installer.

Для сайта и API создай A/AAAA-записи на VPS. Caddy из `deploy/` автоматически запросит TLS после того, как DNS начнёт указывать на сервер и порты 80/443 будут открыты.
