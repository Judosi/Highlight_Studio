# Highlight Studio 10.15.7 — Quality & Performance

10.15.7 — отдельный релиз поверх 10.15.6. Старый архив и папка не перезаписываются.

## 1. Quality Guard

Аппаратный Auto-профиль больше не имеет права ради скорости скрыто уменьшать качество анализа. Сохраняются выбранные `text_model`, `whisper_model`, Ollama context, Visual Scan interval/max samples, OCR sampling/upscale/languages, micro-window и другие quality-critical параметры. Qwen3 8B не заменяется более лёгкой моделью.

## 2. Micro AI / Ollama

В 10.15.6 low-VRAM профиль ошибочно трактовал `micro_batch_size` как число параллельных GPU-задач и мог зажимать его до 1. Но Micro AI выполняет batch последовательно, а за конкуренцию GPU отвечает `gpu_job_limit`. В 10.15.7 legacy Auto-проекты с batch 1 мигрируют на безопасную логическую группировку, по умолчанию micro batch 8. Это уменьшает число повторяющихся Ollama-запросов, не выбрасывая ни одного микро-фрагмента.

Добавлены model warm-up перед Block AI и Micro AI, более строгая инструкция вернуть каждый входной ID, корректный счётчик «готовых» batch и освобождение Qwen-модели перед долгим Visual/OCR этапом. Поздние AI-проходы при необходимости загружают ту же модель снова.

## 3. Visual Scan

Visual coverage не сокращается. На `hardware_decode=auto` приложение коротко сравнивает CPU и `-hwaccel cuda` на реальном source. CUDA выбирается только при успешном выполнении и измеримом выигрыше; иначе остаётся CPU. Full-pass CUDA при ошибке повторяется с тем же sample plan на CPU.

## 4. OCR

Количество OCR sample'ов, Tesseract, языки, ROI и upscale не меняются. Независимые кадры выполняются через bounded ThreadPool. Результаты сортируются обратно по исходному порядку, поэтому ускорение не меняет детерминированный смысл результата.

## 5. UI memory / polling

Во время `running/queued` frontend больше не загружает весь тяжёлый dashboard-state каждые 2.5 секунды. Новый `progress-state` отдаёт только статус, короткую историю, tail логов и Ollama monitor для AI-этапов. После terminal state выполняется один полный refresh.

## 6. ETA и telemetry

Общий ETA теперь имеет нижнюю границу текущего stage ETA. Добавлен `performance_telemetry.json` с длительностью и throughput этапов. В фоне примерно раз в 10 секунд собираются CPU, RAM, а при наличии NVIDIA — GPU utilization и VRAM через `nvidia-smi`.

## 7. Качество

Версия не уменьшает Visual/OCR coverage и не меняет Qwen3 8B ради скорости. Hardware Auto оптимизирует устройство, encoder, worker limits и безопасную batching/concurrency архитектуру. Изменение качества должно происходить только через явные пользовательские task/analysis настройки.

## Quality-first context guard for Micro AI

После финальной проверки добавлено контекстно-безопасное объединение Micro AI: `micro_batch_size` остаётся верхним пределом производительности, но фактическая пачка автоматически дробится, если суммарный prompt может приблизиться к `ollama_num_ctx`. Все микро-окна сохраняются, Qwen3 8B не меняется, и runtime-отчёт фиксирует фактические размеры пачек и `all_windows_preserved`.
