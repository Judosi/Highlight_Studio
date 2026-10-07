# Highlight Studio v9.8.3 — Product Audit Fix

## Что проверено

- Backend API: FastAPI routes, project lifecycle, Twitch import, settings validation, render gates, local auth.
- Frontend UX: guided flow, import, source readiness, render checklist, settings, review/export screens.
- Build/tests/security: frontend production build, backend smoke tests, npm audit.

## Что исправлено

1. **Source readiness в UI**
   - Раньше Twitch-проект мог визуально считаться готовым только потому, что у него был `source_type: twitch`, даже если VOD/Live ещё не был скачан в cache.
   - Теперь UI считает источник готовым только при наличии `source_video_path`.
   - Кнопки рендера заблокированы, пока источник реально не подготовлен.

2. **Финальный чек перед рендером**
   - Исправлена проверка `Источник видео готов`.
   - Она больше не проходит просто из-за существования проекта.

3. **Безопасность frontend dependencies**
   - Обновлены `vite` до `8.0.16` и `@vitejs/plugin-react` до `6.0.2`.
   - `npm audit --audit-level=moderate` теперь показывает `0 vulnerabilities`.

4. **Версионирование**
   - `APP_VERSION` обновлён до `v9.8.3-product-audit-fix`.
   - README обновлён под актуальную продуктовую цель.

## Продуктовая цель

Проект должен решать конкретную проблему: **длинный стрим на 3–6 часов слишком долго резать вручную**.

Правильный сценарий продукта:

1. Импортировать файл или Twitch VOD/Live.
2. Проверить систему и источник.
3. Выбрать стиль нарезки.
4. Запустить AI-анализ через Ollama.
5. Проверить моменты в Review Studio.
6. Собрать итоговый YouTube-ролик.
7. Сгенерировать Shorts, metadata, таймлайн и идеи обложки.

## Проверки после исправлений

- `npm --prefix frontend run build` — успешно.
- `pytest -q` — 31 тест пройден.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.

## Что улучшить дальше

- Добавить полноценный визуальный мастер «Источник → Проверка → Анализ → Review → Рендер» с обязательными статусами.
- Добавить отдельную карточку «Почему момент выбран AI» для каждого кандидата.
- Добавить автодиагностику долгих зависаний: что именно сейчас делает Whisper/Ollama/yt-dlp.
- Добавить пресеты не только под железо, но и под задачу: `IRL смешное`, `Конфликт`, `Спорт`, `Подкаст`, `Shorts only`.
- Добавить итоговый отчёт после рендера: длительность, число клипов, найденные сильные моменты, причины AI, готовые названия.
