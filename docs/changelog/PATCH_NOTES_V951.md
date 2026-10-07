# v9.5.1 — Hardware Optimized for GTX 1050 Ti / 16GB RAM / Ryzen 5 2600

## Added
- Hardware presets for GTX 1050 Ti 4GB + 16GB RAM + Ryzen 5 2600:
  - Fast
  - Balanced: recommended
  - Quality
- Backend endpoint: `POST /api/projects/{project_id}/hardware-preset`.
- New settings:
  - `hardware_profile`
  - `analysis_profile`
  - `ollama_keep_alive`
  - `ollama_num_ctx`
- UI buttons in Settings:
  - `Мой ПК: быстрее`
  - `Мой ПК: быстро + качественно`
  - `Мой ПК: качество`
- Ollama requests now pass `keep_alive` and `num_ctx` to reduce memory pressure and improve batch stability.

## Recommended preset
Use `Мой ПК: быстро + качественно` for normal long Twitch/IRL streams.

## Checks
- `python -m compileall backend tests tools`
- `pytest -q` → 31 passed
- `npm run build` → OK
