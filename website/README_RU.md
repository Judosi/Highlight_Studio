# Сайт Highlight Studio

Сайт статический и использует `website/config.json` для URL загрузки, оплаты, поддержки и юридических страниц.

Не редактируй production config вручную. Заполни `launch/launch_config.json` и выполни:

```bash
python tools/launch/configure_launch.py --config launch/launch_config.json
```

Публикация через GitHub Pages находится в `.github/workflows/deploy-website.yml`. Для собственного VPS сайт раздаёт Caddy из `deploy/docker-compose.yml`.
