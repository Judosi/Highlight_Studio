# Highlight Studio 10.15.10 — AI Runtime & Explainability

## Цель релиза

10.15.10 не меняет смысловую модель отбора 10.15.9 и не заменяет Qwen3 8B. Релиз укрепляет инфраструктуру вокруг AI: единый runtime, предсказуемые retry, локальный repair JSON, честное разделение detected/runtime/effective hardware и детерминированную трассировку причин выбора каждого кандидата.

## Изменения

### 1. Единый AI Runtime
- Добавлен `backend/src/highlight_studio/integrations/ai/runtime.py`.
- Все основные Ollama JSON-запросы проходят через `AIExecutionController`.
- Транспортные ошибки и смысловые/структурные ошибки разделены.
- Транспортный retry budget по умолчанию: 2 попытки, максимум 3.
- Semantic completeness остаётся ответственностью pipeline, поэтому retry budgets не умножаются незаметно.

### 2. Локальный JSON repair
- `parse_json_loose` сначала исправляет распространённые ошибки локально.
- Добавлен безопасный `ast.literal_eval` fallback для Python-подобных ответов Qwen.
- Повреждённый формат сам по себе больше не вызывает отдельный «repair inference» в OllamaClient.

### 3. AI tracing
Для проекта создаются:
- `ai_runtime_trace.jsonl` — события каждого логического AI request;
- `ai_runtime_summary.json` — агрегированная статистика по операциям и ошибкам.

Трассировка не содержит полный prompt, только безопасные метаданные (операция, model, batch, размер prompt, попытка, время).

### 4. GPU Resource Manager
- Добавлен process-wide lease для тяжёлых AI/GPU операций.
- Ollama warmup/generate/unload и активная CUDA-транскрибация Whisper используют один `local_ai_heavy` resource.
- На low-VRAM Auto (`gpu_job_limit=1`) фоновые AI-задачи не должны одновременно вычисляться с Whisper CUDA.

### 5. Hardware readiness
`detect_hardware_capabilities()` теперь содержит `readiness_matrix` и отдельно показывает:
- GPU detected;
- CTranslate2 CUDA runtime ready;
- effective Whisper device;
- NVENC advertised;
- NVENC runtime tested;
- effective encoder;
- NVDEC capability/effective policy.

То есть NVIDIA detected больше не равно «Whisper CUDA работает».

### 6. Candidate decision trace
После полного анализа создаётся `candidate_decision_trace.json`.
Для каждого кандидата сохраняются:
- rank/time/duration;
- selected yes/no;
- точная `selection_reason`;
- semantic class/confidence/evidence;
- AI/transcript/visual/OCR/audio/IRL/penalty scores;
- confidence/standalone clarity;
- storyline/moment type/hook;
- explanation/viewer value/risk.

Этот отчёт вычисляется только из уже готовых результатов и не влияет на ranking или selection.

### 7. API диагностики
Добавлены read-only endpoints:
- `GET /api/projects/{project_id}/ai-runtime`
- `GET /api/projects/{project_id}/candidate-trace`

Dashboard получает только summary candidate trace без большого массива `items`, чтобы не возвращать лишний memory churn браузеру.

## Quality Guard
10.15.10 сохраняет:
- `qwen3:8b`;
- semantic content guard 10.15.9;
- temporal fairness без искусственного 25/25/25/25 распределения;
- visual/OCR coverage;
- quality-first fill behavior;
- reconnect/replay/intermission veto;
- ручной переход в монтаж.

Релиз не снижает число visual/OCR samples и не меняет Qwen на более слабую модель.
