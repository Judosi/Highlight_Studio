# Highlight Studio 11.1.0 — финальные исправления Shorts/Recovery

## Исправлено после аудита

- Ручной `caption_text` теперь является авторитетным текстом SRT/ASS и сохраняет word-level тайминги через сопоставление и интерполяцию.
- Fingerprint одного Short учитывает качество ASR, модель Whisper, словарь распознавания, WhisperX, язык, SenseVoice и reframe plan.
- Реализован optional SenseVoice SER/AED pass по shortlist для смеха/аплодисментов/эмоций без замены русского Whisper ASR.
- Boundary optimizer использует transcript, LLM anchors, speech tail и audio/emotion evidence вместо фиксированных процентов длительности.
- Реализован optional MediaPipe face tracking со сглаживанием траектории и реальный динамический crop.
- Реализован Gameplay + Facecam split layout с поиском устойчивой corner-facecam; Auto выбирает безопасный режим автоматически.
- Dynamic ASS captions получили word-level karaoke + мягкий active-word pop.
- Добавлена visual Shorts safe-zone и настройки качества/кадрирования в Shorts Studio.
- Production Shorts bridge больше не monkey-patch'ит `window.fetch`; React остаётся владельцем workflow state.
- Durable pipeline schema v2 корректно закрывает предыдущую running-стадию при переходе и финализации job.
- Project schema поднята до v4 с идемпотентной миграцией Shorts defaults.

## Fallback-политика

WhisperX, SenseVoice и MediaPipe остаются optional. Их отсутствие или сбой переводит конкретное улучшение в безопасный fallback и не уничтожает готовые Shorts или основной монтаж.

## Проверки

- Targeted backend/release/Shorts regression suite: 50 passed.
- Полный backend suite: 395 passed; 2 environment-only Whisper VAD readiness checks не проходят в Linux-аудит-среде без Windows runtime.
- Frontend contract suite: 83 passed; 2 jsdom tests не запускаются в текущей offline audit environment из-за неполного local `node_modules`. Production `frontend/dist` присутствует и проверяется release tests.
