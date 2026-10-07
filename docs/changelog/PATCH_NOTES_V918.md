# v9.1.8 — OpenAI API Key Check

- Added `POST /api/ai/check-openai` to validate OpenAI/OpenAI-compatible API settings without requiring a project.
- The checker diagnoses missing key, invalid key, bad Base URL, quota/rate-limit, timeout, connection errors and unavailable model names.
- Added Settings UI button: **Проверить API ключ**.
- The result is shown directly in the OpenAI setup card with clear recommendations.
- Updated app version to `v9.1.8-openai-key-check`.
