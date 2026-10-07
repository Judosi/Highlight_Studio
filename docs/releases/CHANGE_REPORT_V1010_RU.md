# Highlight Studio v10.1.0 — отчёт по исправлениям

## Что реально исправлено

### 1. Path traversal через project_id

Было: `project_dir(project_id)` просто делал `PROJECTS_DIR / project_id`.

Стало:

- ID проекта декодируется несколько раз;
- разрешены только символы `A-Z a-z 0-9 _ -` длиной до 64;
- итоговый путь обязан быть внутри `PROJECTS_DIR.resolve()`;
- все подозрительные ID дают 404.

Проверено тестами:

- `..%2Fsecret`;
- `%252e%252e%252fsecret`;
- `abc%2F..%2F..%2Fsecret`;
- `abc..`.

### 2. Локальный токен больше не уходит в URL

Было:

- `/api/health` возвращал `local_auth_token` в JSON;
- frontend сохранял его в `localStorage`;
- ссылки на файлы добавляли `?local_token=...`.

Стало:

- `/api/health` не отдаёт токен в JSON;
- `/api/health` выставляет HttpOnly cookie `highlight_studio_local_token`;
- frontend делает `fetch(..., credentials: 'include')`;
- `authQuery()` больше не добавляет токен в URL;
- query-token auth выключен по умолчанию и включается только через `HIGHLIGHT_STUDIO_ALLOW_QUERY_TOKEN=1`.

### 3. Twitch URL validation

Было: проверка `"twitch.tv" in host`, поэтому `twitch.tv.evil.example` проходил.

Стало: принимается только `twitch.tv` или `*.twitch.tv`.

### 4. `/api/twitch/probe`

Было: `kind` мог стать целым словарём.

Стало: API возвращает нормальный контракт:

```json
{
  "ok": true,
  "kind": "vod",
  "vod_id": "1234567890",
  "channel": null
}
```

### 5. Безопасный export

Было: ZIP проекта мог включать исходное видео, WAV, chunks, outputs и runtime-состояние.

Стало: `/api/projects/{id}/export` делает metadata-export и исключает:

- исходные видео;
- WAV;
- transcript chunks;
- render parts;
- preview/HLS;
- outputs;
- heavy binaries;
- `jobs.sqlite3`;
- `local_auth_token.txt`.

### 6. Release packager

Добавлен `tools/make_release.py`.

Он собирает релиз по allowlist/denylist и не кладёт в архив:

- `.highlight_studio/`;
- `projects/`;
- `node_modules/`;
- `.pytest_cache/`;
- `__pycache__/`;
- `.sqlite3`, `.pyc`, логи, временные и медиафайлы.

### 7. Fingerprint кэша транскрипта

Было: если есть `transcript.json`, он использовался без проверки актуальности.

Стало:

- создаётся `transcript_manifest.json`;
- fingerprint учитывает исходник, Whisper model/device/compute, язык, chunk size и версию;
- при изменении исходника или настроек старый кэш сбрасывается;
- после успешной транскрибации удаляется большой временный `audio_16k.wav`.

### 8. Fingerprint кэша scene detection

Было: scene detection мог запускаться повторно без реального кэша.

Стало:

- создаётся `scene_times_manifest.json`;
- повторный анализ использует кэш, если исходник и threshold совпадают.

### 9. Remove silence + SRT

Было: `silenceremove` мог менять длительность клипов, а SRT оставался в старой временной шкале.

Стало:

- если включены SRT-субтитры, `remove_silence` не применяется;
- в лог пишется причина;
- fingerprint рендера учитывает фактически применённый режим.

### 10. Hardware encoder

Было: интерфейс мог принимать `h264_nvenc`, но backend часто всё равно тихо откатывался на `libx264`.

Стало:

- backend проверяет наличие encoder в текущем FFmpeg;
- если encoder доступен — использует его;
- если недоступен — пишет понятный fallback в лог.

### 11. Более честный режим качества

Добавлены настройки:

- `strict_quality_mode`;
- `strict_quality_min_score`;
- `strict_quality_min_confidence`.

В этом режиме проект не добивает целевую длительность слабыми фрагментами, а честно может вернуть более короткий, но более сильный монтаж.

### 12. Selection report

После анализа создаётся `selection_report.json`:

- выбранная длительность;
- целевая длительность;
- число выбранных сегментов;
- strict/fill режим.

### 13. Усиленный result check

Теперь проверяются:

- размер файла;
- длительность;
- видеопоток;
- разрешение;
- аудиопотоки;
- наличие SRT.

## Проверки, которые я прогнал

```text
python -m py_compile backend/*.py tools/make_release.py
pytest -q
npm run build
npm audit --audit-level=moderate
```

Результат:

```text
60 passed
frontend build OK
npm audit: 0 vulnerabilities
release zip: 110 files, 46.03 MB
runtime/secret files in release zip: 0
```

## Что не могу честно подтвердить здесь

Я не запускал реальную многочасовую Twitch-запись через Whisper/Ollama/FFmpeg до финального MP4, потому что для этого нужны реальные локальные модели, GPU/CPU конкретного компьютера и длинный VOD. Поэтому оценка «9/10» сейчас означает: кодовая база, безопасность релиза и автоматические проверки сильно подтянуты; финальное качество нарезок всё равно нужно подтвердить на 2–3 реальных видео.

