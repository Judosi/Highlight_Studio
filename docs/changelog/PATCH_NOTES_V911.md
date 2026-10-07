# Highlight Studio v9.1.1 — Settings Tooltips

## Что изменилось

- Проверена вкладка **Настройки** после Aurora UI redesign.
- Добавлены все основные настройки, которые были в состоянии приложения, но не были доступны во вкладке настроек:
  - Content type
  - A/B режим
  - Auto mode
  - Auto target duration
  - Target minutes
  - Visual mode
  - Strict preflight
  - Metadata retries
- Добавлены hover-подсказки для всех строк настроек.
- В подсказках объясняется, что делает настройка, когда её лучше менять и какой эффект она даёт.
- Подсказки видны при наведении на значок `?` рядом с названием настройки.
- Сохранены все функции v9.1.0: Fast Import, IRL, AI 100%, OCR, visual scan, HLS/rough preview, metadata, timeline export, thumbnails, compare mode, reports.

## Проверка

```bash
python -m compileall backend tests
pytest -q
# 19 passed

npm --prefix frontend install --no-audit --no-fund
npm --prefix frontend run build
# build OK
```
