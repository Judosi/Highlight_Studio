# Развёртывание сайта и license server

1. Скопируй `.env.example` в `.env`.
2. Подставь реальные домены и секреты.
3. Направь DNS A/AAAA-записи двух доменов на сервер.
4. Запусти `docker compose up -d --build` из папки `deploy`.
5. Проверь `https://<домен>/` и `https://<api-домен>/health`.
6. Сделай резервное копирование Docker volume `license_data`.

Caddy автоматически получает TLS-сертификаты. Приватный Ed25519-ключ, admin token и webhook secret нельзя хранить в Git или передавать покупателям.
