# v9.8.8 Clean Dark Product UI

Исправлена реальная проблема смешивания старого светлого UI и нового тёмного дизайна.

Что сделано:
- `frontend/src/styles.css` переписан как единая clean dark design-system, а не как набор патчей поверх старого CSS.
- Убраны старые белые карточки, белые панели, светлые readiness/import/action/review блоки.
- Все формы, кнопки, настройки, карточки, Project Snapshot, System Readiness, Fast Import, Twitch Import, Review Studio и Autopilot приведены к одной тёмной glass-системе.
- Старые функции не удалялись: backend/API, импорт, Twitch, Ollama, Whisper, OCR, Visual Scan, task-presets, resume/checkpoints, preflight, render и reports сохранены.
- Версия обновлена до `v9.8.8-clean-dark-product`.

Главная цель версии: рабочий продукт с единым дизайном без конфликта двух тем.
