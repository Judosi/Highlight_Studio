# Highlight Studio v9.4.0 — Stable Mode Product

Главная цель релиза: сделать приложение полезнее и понятнее без удаления функций.

## Что добавлено

- Stable / Pro режим в шапке. Stable показывает безопасный сценарий, Pro оставляет все профессиональные настройки.
- Блок «Следующий шаг» после статуса: приложение подсказывает, что делать дальше.
- Кнопка «Тест 10 минут» для Twitch VOD, чтобы не начинать сразу с 3-часового стрима.
- Пресеты Metadata: 8 / 12 / 20 / 25 clips вместо опасных ручных 80.
- Безопасные настройки Ollama одной кнопкой.
- Более дружелюбные ошибки вместо сырых WinError/HTTPConnectionPool/401.
- Улучшенный Twitch progress: cache size, speed MB/s, elapsed time во время yt-dlp/Live cache.
- Обновлённая верхняя панель: «Найти моменты», «Собрать видео», Stable/Pro.

## Что сохранено

Fast Import, Twitch Import, IRL, OCR, Visual scan, AI Strict, Metadata, Render, Shorts, Export, Reports, Compare mode, Project Manager.

## Проверка

- python -m compileall backend tests tools — OK
- pytest -q — 27 passed
- npm run build — OK
- /api/health — OK
- frontend / — OK
