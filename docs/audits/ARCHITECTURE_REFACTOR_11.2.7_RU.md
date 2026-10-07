# Архитектурный рефакторинг Highlight Studio 11.2.7

## Цель

Улучшить структуру проекта без изменения пользовательского поведения, формата существующих проектов, portable-запуска через `START_HERE.bat`, API-маршрутов и формата лицензии.

## Что было найдено

Проект в целом уже имел правильное верхнеуровневое разделение (`backend`, `frontend`, `desktop`, `services`, `tools`, `docs`, `tests`, `vendor`). Главный технический долг находился не в количестве папок, а в трёх очень крупных владельцах логики:

- `backend/src/highlight_studio/api/app.py` — 6874 строки до рефакторинга;
- `backend/src/highlight_studio/services/pipeline.py` — 11738 строк до рефакторинга;
- `frontend/src/app/App.jsx` — около 3940 строк.

Дополнительно в корне `frontend/` лежал временный `task-center-fixture.jsx`, относящийся только к тестам.

## Выполненные изменения

1. **API-схемы отделены от маршрутов.** `AppSettings` и request-модели вынесены в `api/schemas.py`. `api.app` импортирует и реэкспортирует те же классы, поэтому существующие импорты не ломаются.
2. **Domain-модели pipeline отделены от orchestration.** `Candidate` и `TranscriptSegment` вынесены в `services/media_models.py` без I/O-зависимостей.
3. **Чистая AI policy отделена от media pipeline.** Batching, completeness, retry policy и нормализация AI-ответов вынесены в `services/ai_policy.py`.
4. **Тестовый fixture убран из runtime-корня frontend.** `taskCenter.test.js` создаёт временный fixture рядом с тестом и удаляет его после выполнения.
5. **Добавлен архитектурный guard.** `tools/quality/architecture_audit.py` проверяет, что `core` не зависит от верхних слоёв, `services/integrations/infrastructure` не импортируют API, root релиза не захламляется, а compatibility wrappers не превращаются во вторые реализации.
6. **Launcher-сообщение синхронизировано с release-тестом.** Ошибка распаковки теперь явно содержит предупреждение не запускать BAT внутри ZIP.
7. **Release-identity test синхронизирован с фактическим portable-поведением.** Валидная распакованная папка может получить Windows-суффикс `(1)`; проверяется содержимое/identity, а не косметическое имя папки.

## Что намеренно не делалось

Не выполнялся массовый перенос функций из `pipeline.py`, `app.py` и React `App.jsx`. Эти файлы содержат много проверенного поведения, monkeypatch/contract tests и shared state. Механическое дробление дало бы красивое дерево ценой более высокого риска регрессий. Следующий безопасный этап — выделять по одному route-domain и по одному pipeline stage с characterization tests.

## Проверка

`python tools/quality/architecture_audit.py` после изменений: **0 errors, 3 warnings**. Три warning — только крупные модули, оставленные как явный технический долг.

Python regression suite был прогнан тремя независимыми группами: **568 passed**, **2 environment-dependent failed**, **1 skipped**. Две ошибки относятся к runtime probe Whisper/VAD из-за отсутствия `faster-whisper`/`onnxruntime` в текущей Linux-среде; один skip — отсутствующий `mediapipe`. Эти пакеты перечислены в `backend/requirements.txt` и штатно устанавливаются Windows launcher'ом.

Frontend suite в частично установленном Node окружении: **107 tests passed**; DOM-тесты, требующие `jsdom`, не смогли стартовать из-за прерванного `npm ci` в среде проверки. Изменение production frontend source не выполнялось; изменён только путь временного test fixture.

## Итог

Архитектура стала яснее без изменения контрактов продукта. Самые рискованные монолиты не «переписаны ради красоты»: они отмечены автоматическим guard как следующие цели и могут декомпозироваться поэтапно с сохранением поведения.
