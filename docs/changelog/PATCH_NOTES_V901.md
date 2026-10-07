# Highlight Studio v9.0.1 — Button Check

- Исправлены endpoint’ы Timeline Export и Compare Mode: теперь без сегментов/кандидатов возвращают понятный `ok:false`, а не 500.
- Export-кнопки теперь блокируются до появления нужных данных: render/timeline/shorts/factory ждут финальные сегменты, compare ждёт AI-кандидаты.
- UI показывает понятное сообщение, если команда выполнена, но данных для результата ещё нет.
- Backend/frontend сборка и smoke-тесты проверены.
