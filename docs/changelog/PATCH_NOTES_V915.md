# v9.1.5 — Gemini Hybrid AI Engine

Добавлено:

- Новый AI Engine в настройках и стиле:
  - `Ollama Local` — полностью локально.
  - `Gemini API` — scoring/metadata/vision через Gemini API.
  - `Hybrid Auto` — Gemini при наличии API key, fallback на Ollama.
- Gemini API настройки:
  - `Gemini API key` или переменная среды `GEMINI_API_KEY`.
  - `Gemini text model`.
  - `Gemini vision model`.
  - `Gemini timeout`.
  - `Gemini privacy`: `local_only`, `transcript_only`, `frames_allowed`.
  - `Gemini temperature`.
  - `Hybrid fallback`.
- Backend AI router: единый клиент `make_ai_client(settings)`, который маршрутизирует запросы в Ollama/Gemini/Hybrid.
- Preflight теперь проверяет не только Ollama, а выбранный AI Engine.
- AI cache fingerprint теперь меняется при смене effective model, поэтому Gemini/Ollama не смешивают старые batch-кэши.
- Настройки валидируются и сохраняются через Pydantic.
- Добавлены smoke tests для Gemini settings/effective models.

Важно:

- Для Gemini нужен API key. Укажи его во вкладке `Настройки` или задай переменную среды `GEMINI_API_KEY`.
- Для приватности по умолчанию стоит `transcript_only`: в Gemini отправляется текст, но не кадры.
- Для IRL visual boost включай `frames_allowed`, иначе Gemini не будет получать кадры.
- Если выбран `Gemini API` без ключа, preflight покажет ошибку.
- Если выбран `Hybrid Auto`, приложение попробует Gemini, а при ошибке fallback на Ollama, если fallback разрешён.
