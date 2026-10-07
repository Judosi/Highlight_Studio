# Highlight Studio v8.9.0 — Ideal Polish

Эта версия не обещает магическое «идеально на любом видео», но закрывает самые опасные места из аудита v8.8.0:

## Стабильность
- Browser upload больше не копирует файл дважды и ограничен 2 GB. Большие стримы должны идти через Fast Import без копирования.
- AI metadata больше не блокирует основной analyze job. AI Metadata и AI Metadata 100% запускаются отдельными background jobs.
- Версия проекта обновлена до `v8.9.0-ideal-polish`.

## Скорость IRL/Visual
- Visual scan теперь извлекает кадры одним FFmpeg-процессом (`fps=1/N`), а не тысячами отдельных запусков FFmpeg.
- Оставлен fallback на старый per-frame режим, если bulk extraction не сработал.

## OCR
- Добавлены настройки OCR languages, ROI-зона чата/донатов, upscale и grayscale-preprocess.
- OCR fingerprint учитывает ROI/languages/upscale, поэтому кэш не смешивает разные настройки.

## Timeline export
- EDL/FCPXML теперь используют FPS/размер исходника из ffprobe, а не фиксированные 25 fps.

## Честное ограничение
Полный visual understanding всего IRL-стрима всё ещё зависит от внешней vision-модели и мощности ПК. v8.9 ускоряет и укрепляет pipeline, но не превращает лёгкий scan в полноценный просмотр каждого кадра нейросетью.
