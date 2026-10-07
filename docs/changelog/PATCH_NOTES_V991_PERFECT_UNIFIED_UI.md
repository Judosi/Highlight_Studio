# Highlight Studio v9.9.1 Perfect Unified UI

Цель версии: убрать смешение старого и нового дизайна в самом React-приложении, а не только перекрасить CSS.

## Исправлено

- Task-пресеты теперь имеют auto-fit сетку и нормальный перенос текста.
- Убраны переполнения карточек и длинных описаний.
- Настройки больше не являются декоративным экраном: добавлены вкладки, поиск, редактирование и сохранение.
- Сохранение настроек работает без проекта: параметры сохраняются в локальный профиль и применяются к новому проекту после импорта.
- При создании проекта из upload/Fast Import/Twitch текущие настройки автоматически сохраняются в проект.
- CSS переписан как единая dark/glass дизайн-система.

## Сохранено

- Import / Fast Import / Twitch VOD / Twitch Live.
- Ollama, Whisper, OCR, Visual Scan.
- Task presets, Autopilot, Preflight, Resume, Cache fingerprint.
- Review Studio, render, reports, creator pack.

## Проверка

- Frontend build проходит.
- Backend py_compile проходит.
- Backend tests должны проходить как в v990, потому что backend-логика не ломалась.
