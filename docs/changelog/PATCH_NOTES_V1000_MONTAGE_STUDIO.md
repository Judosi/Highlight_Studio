# Highlight Studio v10.0.0 — Montage Studio

Безопасная доработка вкладки **Монтаж / Review Studio** поверх v9.9.9 Quality Doctor.

## Что добавлено

- Timeline map: отдельная дорожка Final и отдельная дорожка AI-кандидатов по времени исходного видео.
- Кликабельные сегменты на timeline: нажатие открывает выбранную микро-нарезку в инспекторе.
- Clip player: отдельный большой проигрыватель выбранной микро-нарезки с авто-остановкой на `end`.
- Mini player в карточках моментов: можно раскрыть проигрыватель прямо под конкретной микро-нарезкой.
- Улучшенный список моментов: номер, таймкод, длительность, score, объяснение AI и быстрые действия.
- Переключение AI-кандидаты / Финальный монтаж внутри одной аккуратной панели.
- Улучшенные CSS-правила для Review Studio: adaptive layout, sticky player, responsive timeline.

## Что не трогалось

- Twitch Turbo Downloader, TDCLI, aria2c, yt-dlp fallback.
- Backend pipeline анализа, Whisper, Ollama, OCR, Visual Scan.
- Render, Export, YouTube/Shorts helper.
- Safety Guard, Product Health, Project Doctor, cache fingerprint.

## Проверка

- `npm --prefix frontend run build` — успешно.
- `pytest -q` — 47 passed.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py backend/twitch_source.py backend/utils.py` — успешно.
