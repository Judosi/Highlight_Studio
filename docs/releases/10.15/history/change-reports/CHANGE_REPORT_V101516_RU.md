# Highlight Studio 10.15.16 — Review Unlock Hotfix

## Подтверждённая ошибка

В реальном проекте анализ завершился со `state=done`, `analysis_complete=true`, 126 кандидатами и 49 сегментами. `pre_render_check.json` был валиден, но dashboard скрывал результаты и интерфейс блокировал «Монтаж».

## Причина

10.15.15 включала `whisper_device` и `whisper_compute` в semantic analysis revision. Auto hardware менял их только для исполнения (`cpu/int8` → `cuda/int8_float32`), поэтому `mark_analysis_complete()` сохранял runtime revision, а последующий dashboard вычислял revision из persisted settings. Хэши не совпадали, хотя контент анализа не менялся.

## Исправление

- Runtime-only hardware поля исключены из semantic analysis revision.
- Добавлена точная миграция legacy revision 10.15.15 с доказательством через `hardware_runtime.json`.
- Миграция не выполняется при реальном изменении semantic settings (например, модели/промта/preset).
- После восстановления freshness backend снова отдаёт `candidates` и `segments`; существующая ручная навигация React разблокирует «Монтаж».
- Исправлен CSS onboarding checkbox, конфликтовавший с глобальным `.studioApp.studioAppV2 input`.

## Проверка

Regression tests: `tests/test_review_unlock_101516.py`.
Frontend contract tests подтверждают ручную навигацию и packaged production bundle.
