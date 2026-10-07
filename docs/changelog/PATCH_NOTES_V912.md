# Highlight Studio v9.1.2 — Real Progress + ETA

## Что изменилось

- Полоса выполнения теперь получает данные из `status.json` с реальными счётчиками этапов, а не только статичные проценты.
- Добавлены поля прогресса:
  - `progress_source`: `real_counter`, `heartbeat`, `estimated_global`, `done`, `error`, `cancelled`.
  - `stage_progress_percent` / `batch_progress_percent`.
  - `current_batch` / `total_batches`.
  - `stage_eta_seconds` — примерное время до конца текущего этапа.
  - `eta_seconds` — примерное время до конца всей задачи.
  - `elapsed_seconds` — сколько уже прошло.
  - `items_per_minute` — скорость обработки для этапов со счётчиком.
  - `remaining_items` — сколько элементов осталось.
- UI теперь показывает две полосы:
  - общий прогресс задачи;
  - прогресс текущего этапа.
- Улучшены этапы с реальными счётчиками:
  - транскрибация chunk X/Y;
  - AI block batch X/Y;
  - vision-кандидаты X/Y;
  - visual scan X/Y;
  - OCR scan X/Y;
  - render parts X/Y;
  - rough preview parts X/Y;
  - shorts render X/Y;
  - AI metadata attempts X/Y.
- Добавлена история статусов `status_history.json` для диагностики прогресса.

## Ограничения

- Для отдельных длинных FFmpeg-команд прогресс внутри одного процесса всё ещё может обновляться только между этапами/после завершения команды. Но для основных циклов теперь используются реальные счётчики и ETA.
- ETA является оценкой: скорость Ollama/Whisper/FFmpeg может меняться во время работы.
