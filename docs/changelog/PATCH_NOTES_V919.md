# v9.1.9 — Ollama Only

- Cloud API режимы убраны из интерфейса.
- OpenAI/Gemini settings больше не сохраняются в project.json.
- Все старые cloud AI проекты автоматически мигрируют на `ai_engine=ollama`.
- AI router снова отправляет все text/vision запросы в Ollama.
- Сохранены Twitch Import, IRL, OCR, Visual scan, AI 100%, progress ETA и новый UI.
- Добавлены smoke tests на миграцию cloud settings обратно в Ollama.
