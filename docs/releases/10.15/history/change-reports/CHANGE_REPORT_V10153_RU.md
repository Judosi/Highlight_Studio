# Highlight Studio 10.15.3 — полный аудит frontend

## Почему сделан отдельный аудит

Предыдущие версии исправляли отдельные симптомы. В 10.15.3 проверен весь пользовательский frontend: исходный React, production bundle, HTML, CSS-каскад, навигация, восстановление проекта, sidebar, Task Center, Source/Format/Analysis/Review/Export, YouTube Publisher и адаптивная геометрия.

## Подтверждённые дефекты и исправления

1. **Race condition при запуске.** Сохранённый шаг мог проверяться до загрузки последнего проекта и сбрасываться на «Проекты». Workflow guard теперь ждёт завершения `appBooting`.
2. **Старый frontend из browser cache.** Backend отдаёт asset-файлы как immutable на год, а предыдущие ручные сборки сохраняли старое имя bundle. В 10.15.3 JS/CSS assets получили release-specific имена `index-10153-*`, а финальный CSS — `studio-final-10153.css`.
3. **CSS из нескольких поколений.** Production HTML больше не подключает отдельные v4/v5/v6/v7 patch-файлы. Они консолидированы в один финальный слой после v3.
4. **Compact sidebar.** Физическая ширина закреплена в 82 px для всех density-состояний; длинные подписи не могут расширять колонку.
5. **«Новый проект».** `import` разрешён до существования project, поэтому навигационный guard не отменяет открытие экрана источника.
6. **Twitch VOD во время загрузки.** Пока `source_video_path` не готов, приложение остаётся на «Источник» и не показывает заблокированный «Формат».
7. **Task Center.** Активная задача не раскрывает панель автоматически; drawer открывается вручную, fixed-позиционирован и не меняет layout. Горизонтального скролла внутри нет.
8. **Длинные имена Twitch-проектов.** Добавлены min-width/ellipsis/wrap-правила для sidebar, topbar и Source context.
9. **Stale dashboard response.** Медленный ответ от ранее открытого проекта теперь отбрасывается и не может перезаписать новый выбранный проект.
10. **Новые вкладки файлов.** Локальные download/export окна открываются с `noopener,noreferrer`.
11. **YouTube Publisher.** Увеличены controls, добавлены aria-live/progress semantics, адаптивность и безопасный вывод динамических данных.

## Реальные проверки

- HTML: 4 файла разобраны parser-ом; duplicate id / img без alt / небезопасных `_blank` ссылок в HTML не найдено.
- CSS: 13 файлов разобраны `tinycss2`, синтаксических ошибок 0.
- Production layout: Chromium, 8 экранных размеров × 7 пользовательских состояний = 56 layout cases. Горизонтальных overflow/out-of-viewport failures: 0.
- В browser test подтверждено: compact sidebar = 82 px; «Новый проект» открывает Source; Twitch download остаётся на Source; Task Center закрыт по умолчанию и открывается вручную без horizontal scroll.
- Все plain JS production/public/test файлы проходят `node --check`.
- Frontend tests без отсутствующего `jsdom`: все запущенные файлы проходят.
- Python: 192 tests passed; 2 smoke tests не проходят в текущем окружении из-за отсутствующего runtime Whisper Silero VAD.

## Что не называем «идеальным» без проверки на пользовательском ПК

`App.jsx` остаётся большим legacy-компонентом (3191 строк), а базовый CSS до финального слоя исторически сложный. Полная декомпозиция React/CSS — отдельная безопасная миграция и не должна смешиваться с hotfix-релизом. Реальный Windows native file picker, GPU/FFmpeg/Whisper runtime и долгий настоящий Twitch VOD должны быть дополнительно проверены на целевой машине.
