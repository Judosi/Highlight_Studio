# v9.8.6 — Autopilot Reliability Update

База: `v9.8.5-v983-plus-task-presets`.

## Главное
Добавлен следующий слой продукта без удаления старой функциональности:

1. **Умный авто-режим “Собрать нарезку”**
   - `/api/projects/{project_id}/auto-probe`
   - `/api/projects/{project_id}/auto-recommendation`
   - анализирует длительность, FPS/разрешение, источник, вес файла, аудио, признаки чата/OCR и предлагает task/hardware preset.

2. **Умная диагностика перед запуском**
   - `/api/projects/{project_id}/smart-preflight`
   - проверяет video path, FFmpeg/FFprobe, Ollama, text/vision model, Whisper, yt-dlp, место на диске, права записи, target duration.

3. **Живой статус зависаний**
   - расширена диагностика Whisper/Ollama/yt-dlp с конкретной причиной и действием.

4. **Resume после сбоя**
   - `/api/projects/{project_id}/checkpoints`
   - `/api/projects/{project_id}/resume`
   - показывает этапы: source/audio/whisper/visual/OCR/AI/candidates/segments/render.

5. **Безопасный cache fingerprint**
   - `/api/projects/{project_id}/cache-fingerprint`
   - `/api/projects/{project_id}/clear-incompatible-cache`
   - fingerprint зависит от video/settings/prompt/preset/segments/model/app_version.

6. **Review Studio улучшен**
   - фильтры: смешное, конфликт, реакция, донат/чат, спорт, эмоция, диалог, тишина, слабый, повтор;
   - в объяснении добавлен риск;
   - быстрые действия +5 секунд до/после, укоротить, оставить.

7. **Контроль итоговой длительности**
   - `/api/projects/{project_id}/duration-control`
   - действия: добрать похожие, снизить score, добавить контекст, добавить слабее, сделать плотнее, оставить как есть.

8. **Качество перед рендером**
   - `/api/projects/{project_id}/quality-before-render`
   - проверяет сегменты, длительность, средний score, дубли, слишком короткие моменты, missing files, свободное место.

9. **Hardware-пресеты сохранены и усилены**
   - GTX 1050 Ti fast/balanced/quality;
   - Stable/Pro mode сохранены.

10. **Twitch диагностика**
   - `/api/twitch/probe`
   - статусы в UI подсказывают cookies/format/threads/range.

11. **Режим новичка/профи сохранён**
   - Stable Mode показывает главное;
   - Pro Mode сохраняет все старые настройки.

12. **История проектов**
   - `/api/project-history`
   - показывает последние проекты, статус, пресет, кандидаты, сегменты, итоговый файл.

13. **YouTube/Shorts Creator Pack**
   - `/api/projects/{project_id}/creator-pack`
   - создаёт 10 названий, описание, теги, главы, идеи превью и 5 лучших Shorts.

## Проверки
- `pytest -q` — 34 passed.
- `npm --prefix frontend run build` — passed.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.

## Ограничение проверки
Полный end-to-end анализ реального видео не запускался в контейнере, потому что он требует локальных FFmpeg/Ollama/Twitch/видеофайлов пользователя. Кодовые проверки, сборка и backend-тесты пройдены.
