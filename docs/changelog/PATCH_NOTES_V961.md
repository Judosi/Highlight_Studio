# v9.6.1 — AI JSON Rescue

Fixes Ollama/Qwen local-model malformed JSON failures in strict AI mode.

- Added stronger local JSON repair for missing commas and common local LLM mistakes.
- Ollama text/vision requests now use temperature=0 for more stable JSON.
- If Ollama returns malformed JSON, the app asks the same model to repair its own JSON response.
- If a multi-item AI batch still fails in AI 100% mode, the pipeline now falls back to one-ID-at-a-time rescue instead of failing the whole project immediately.
- Same rescue flow added for micro-cut batches.

This keeps AI strict mode honest: it still requires every ID, but it now tries much harder to complete the batch before raising an error.
