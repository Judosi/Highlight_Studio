# v10.0.4 Progress Guard

Безопасная правка обратной связи UI для долгих задач. Рабочий AI/render pipeline не менялся.

## Исправлено

- Backend теперь пишет `queued` status.json сразу при старте background-задачи, до запуска worker-потока.
- Background JobLogger больше не сбрасывает статус в `ready 0%` в начале worker-а. Раньше из-за этого UI мог на первом refresh увидеть старый/пустой статус, и полоска ожидания пропадала, хотя рендер/Shorts выполнялись правильно.
- Frontend теперь держит optimistic progress несколько секунд после нажатия Render / Shorts / Analyze, пока backend не вернёт реальный running/queued/done/error status.
- Live Render / Shorts panel получает более стабильный первый статус и не выглядит пустым сразу после клика.

## Проверки

- `pytest -q` — 53 passed.
- `npm --prefix frontend run build` — OK.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py backend/twitch_source.py backend/utils.py` — OK.
