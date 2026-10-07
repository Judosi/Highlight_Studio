# Highlight Studio 10.15.5 — анализ и remediation запуска One-click

## Причина выпуска

В 10.15.4 кнопка `Начать анализ` была привязана к обработчику, но перед отправкой `POST /api/projects/{id}/one-click` обработчик сохранял настройки через `saveSettings()`. Сохранение синхронно ожидало полный `dashboard-state`. Dashboard выполняет runtime/preflight/Ollama-проверки, поэтому при медленном или недоступном Ollama пользователь мог несколько секунд видеть отсутствие реакции, а сам POST запуска ещё не отправлялся.

Отдельно UI 10.15.4 предлагал `analysis_profile=safe`, тогда как backend разрешал только `fast`, `balanced`, `quality`. При сохранённом `safe` settings endpoint возвращал 422 ещё до вызова one-click.

## Изменения

1. `saveSettings(..., {refresh:false})` позволяет командным кнопкам сохранять настройки без блокирующего dashboard refresh.
2. `runOneClickPipeline()` немедленно создаёт optimistic status, сохраняет нормализованные настройки, отправляет `/one-click`, затем обновляет dashboard.
3. Legacy `safe` мигрируется в `fast` и на frontend, и на backend.
4. `/one-click` для уже подготовленного источника синхронно выполняет source gate + smart preflight, поэтому заведомый runtime/source failure возвращается пользователю сразу, а не после старта background thread.
5. Structured API errors выводят recommendation и первый обязательный failed check.
6. `_ollama_probe` кэшируется на 5 секунд для повторных dashboard-проверок; `/system-check`, `/smart-preflight` и one-click gate используют `force=True`.
7. Smart preflight синхронизирован с `pipeline.preflight`: Light visual mode не требует vision LLM; готовый Twitch VOD не требует downloader; статический порог 5 GB не блокирует анализ.

## Проверки

- Специализированные backend regression tests: 7/7 passed.
- Специализированные frontend contracts: 4/4 passed.
- Основной Python regression subset: 168 passed, 2 failed только на runtime probe Whisper VAD, поскольку в Linux-среде проверки отсутствует установленный `onnxruntime`/VAD runtime. Эти зависимости присутствуют в `backend/requirements.txt` и устанавливаются Windows setup-скриптом.
- Полная release identity и archive verification выполняются перед выдачей ZIP.

## Остаточный риск

Полноценный Windows запуск с реальным локальным Ollama/FFmpeg/Whisper нельзя физически воспроизвести в Linux-среде сборки. Поэтому код и release archive проверяются автоматическими regression/contract тестами, а отсутствие Windows runtime не выдаётся за успешный end-to-end прогон.
