# Highlight Studio v9.9.9 — Quality Doctor

Цель версии: закрыть реальные минусы проекта без ломки рабочего pipeline. Это стабилизационная и продуктовая версия поверх v9.9.8 Product Hardening.

## Что добавлено

### 1. Project Doctor
Безопасная автопочинка проекта, которая не запускает Ollama, FFmpeg, скачивание или рендер.

Делает:
- создаёт недостающие служебные папки `outputs`, `preview`, `debug`, `cache`, `factory`;
- создаёт недостающие безопасные JSON-файлы: `status.json`, `candidates.json`, `segments.json`, `user_preferences.json`;
- восстанавливает повреждённые JSON из `.bak`, если возможно;
- нормализует финальные сегменты: сортировка, id, отрицательные start, слишком короткие фрагменты, явные дубли;
- добавляет недостающие объяснения моментов локально, без тяжёлого AI-запроса;
- обновляет Product Health, Safety Guard и AI Quality Audit.

Новый endpoint:

```txt
POST /api/projects/{project_id}/project-doctor
```

### 2. AI Quality Audit
Проверяет, насколько Review Studio готов к нормальной ручной проверке.

Смотрит:
- количество кандидатов и финальных фрагментов;
- сильные и слабые кандидаты;
- моменты без объяснений;
- слишком короткие фрагменты;
- риски отсутствия контекста у сильных моментов;
- риск повторов;
- распределение по типам: смешное, конфликт, чат/донат, спорт, диалог.

Новый endpoint:

```txt
GET /api/projects/{project_id}/ai-quality-audit
```

### 3. Backfill explanations
Старые candidates/segments теперь можно автоматически обогатить полями:

- `what_happens`
- `why_selected`
- `viewer_value`
- `risk`
- `ai_explanation`

Это работает локально и быстро, без вызова Ollama.

Новый endpoint:

```txt
POST /api/projects/{project_id}/backfill-explanations
```

### 4. Normalize Segments
Добавлена безопасная нормализация финального монтажа перед render:

- сортировка по времени;
- последовательные id;
- отрицательный start → 0;
- слишком короткий фрагмент растягивается до безопасного минимума;
- удаляются явные дубли;
- удаляются некорректные end <= start.

Новый endpoint:

```txt
POST /api/projects/{project_id}/normalize-segments
```

### 5. Acceptance Test Plan
Добавлен план ручных тестов, который помогает понять, когда проект уже можно считать продуктом, а не beta/MVP.

Проверяет сценарии:
- локальный тест 5–10 минут;
- Twitch speed-test;
- Review Studio quality;
- Resume после остановки;
- Cache fingerprint;
- длинный VOD 3–6 часов.

Новый endpoint:

```txt
GET /api/projects/{project_id}/product-test-plan
```

### 6. Dashboard-state расширен
Один общий endpoint теперь отдаёт также:

- `ai_quality_audit`
- `project_doctor`
- `product_test_plan`

Это сохраняет тихий режим и не возвращает старый шум из десятков GET-запросов.

## UI

В разделе `Отчёты` добавлены:

- Project Doctor;
- AI Quality Audit;
- Acceptance Test Plan;
- кнопка Backfill explanations;
- кнопка Normalize segments.

В `Review Studio` добавлена верхняя плашка AI Quality, чтобы перед рендером было видно, насколько хорошо подготовлены моменты.

## Безопасность

Все новые действия безопасные:

- не скачивают VOD;
- не запускают Ollama;
- не запускают FFmpeg render;
- не удаляют исходное видео;
- работают только с JSON-проектом и финальными сегментами;
- используют atomic write / backup слой из v9.9.7.

## Проверка

- `pytest -q` — 47 passed
- `npm --prefix frontend run build` — OK
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py backend/twitch_source.py backend/utils.py` — OK
