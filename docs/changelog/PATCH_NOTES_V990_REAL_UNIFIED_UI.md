# Highlight Studio v9.9.0 — Real Unified UI

Эта версия исправляет главную проблему предыдущих сборок: в реальном React-приложении старый интерфейс продолжал рендериться рядом с новым дизайном. В v9.9.0 старые visual-блоки больше не используются в основном сценарии.

## Что сделано

- Основной экран переписан как единый продуктовый UI: Источник → Настройки → Анализ → Review → Экспорт.
- Старые визуальные секции `Готовность системы`, `Fast Import`, `Twitch Import`, `Обычная загрузка`, `Project Manager` больше не рендерятся как отдельный старый dashboard.
- Функциональность сохранена: Fast Import, Twitch VOD/Live, ручная загрузка, проверка системы, Project Manager, Ollama, Whisper, OCR, Visual Scan, task-пресеты, smart preflight, resume, fingerprint cache, Review Studio, duration control, pre-render quality, YouTube/Shorts pack.
- Старые функции переупакованы в новые блоки:
  - выбор источника;
  - карточка проекта;
  - компактный preflight;
  - аккуратный проектный менеджер;
  - advanced-инструменты без старого внешнего вида.
- `renderActiveStep()` теперь использует только `renderUnified*` экраны.
- CSS дополнен отдельной дизайн-системой `.u*`, которая не зависит от старых карточек.

## Проверка

- `npm --prefix frontend run build` — OK.
- `pytest -q` — 34 passed.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py` — OK.

## Важно

Старая backend-логика и API не удалялись. Удалён именно старый визуальный сценарий из основного React-render. Старые helper-функции могут оставаться в коде как запасные, но пользователь их больше не видит в основном UI.
