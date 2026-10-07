# v9.1.7 — OpenAI API вместо Gemini

- Gemini удалён из UI как основной cloud engine.
- Добавлен OpenAI API engine.
- Добавлен OpenAI-compatible режим через `/v1/chat/completions`.
- Добавлены настройки:
  - `OpenAI API key` / `OPENAI_API_KEY`.
  - `OpenAI Base URL` / `OPENAI_BASE_URL`.
  - `OpenAI text model`.
  - `OpenAI vision model`.
  - `OpenAI timeout`.
  - `OpenAI privacy`: `local_only`, `transcript_only`, `frames_allowed`.
  - `OpenAI-compatible mode`.
- Hybrid Auto теперь использует OpenAI first + Ollama fallback.
- Старые проекты с `ai_engine=gemini` автоматически мигрируют на `openai`.
- Сохранены Twitch Import, AI 100% repair, Workflow UI, tooltips и все вкладки.
