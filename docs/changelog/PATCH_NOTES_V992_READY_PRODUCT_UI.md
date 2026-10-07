# v9.9.2 Ready Product UI

Исправления по пользовательским скриншотам:

- Добавлен настоящий стиль для `StepHeader` / `stepHero`: заголовки вкладок больше не выглядят как сырой текст на чёрной полосе.
- Исправлена вкладка “Стиль”: форма, поля и кнопки больше не съезжают; кнопка “Сохранить настройки” работает даже без проекта, сохраняя локальный профиль.
- Исправлен верхний live-progress/job bar: общий прогресс, stage, ETA, детали и phase rail больше не слипаются в одну строку.
- Добавлены стили для `progressCaption`, `phaseRail`, `phaseItem`, `stageProgressBox`, `bottomMeta`.
- Улучшена страница настроек: сетка полей, вкладки, action-кнопки и редактор теперь стабильны на разных ширинах экрана.
- Старый backend/API и рабочие функции сохранены: импорт, Twitch, Ollama, Whisper, OCR, Visual Scan, Autopilot, Resume, Cache fingerprint, Review Studio, Render и YouTube/Shorts.

Проверки:

- `npm --prefix frontend run build` — успешно.
- `pytest -q` — 34 passed.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py` — успешно.
