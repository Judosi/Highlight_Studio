# Технический аудит Highlight Studio 11.2.3

## Гарантии исправления

1. Исчерпанная временная transport retry policy возвращает `AITransportError`, а не исходный `requests.ReadTimeout`.
2. Успешный Ollama warmup сбрасывает process-local circuit breaker до targeted rescue.
3. Первый transport stall переводит оставшийся Micro AI в single-window режим, не удаляя и не объединяя окна.
4. Recovery ограничен общим budget, поэтому постоянная недоступность Ollama не создаёт бесконечный цикл.
5. Реальное покрытие считает только AI/cache элементы; degraded fallback не выдаётся за AI-анализ.
6. При покрытии ниже порога pipeline останавливается до записи новых `candidates.json` и `segments.json`.
7. `analysis_health.json` явно помечает результат как неполный и возобновляемый; существующие сегменты остаются неизменными.
8. Per-window fingerprints не зависят от номера и размера batch, поэтому смена 8 → 4 и последующий single rescue используют прежние checkpoints.

## Проверки

- transport normalization regression;
- timeout → warmup recovery → single-window success → 100% coverage;
- low-memory automatic batch cap и manual override;
- incomplete 56/144 gate с сохранением прежнего монтажа;
- isolated 91% normal-mode tolerance;
- полный backend/regression suite: 421 passed;
- frontend contract suite без двух jsdom-only файлов: 83 passed;
- Electron security suite: 10 passed;
- production JavaScript syntax check: пройден.

## Ограничения

- Приложение не может гарантировать ровно целевую длительность, если после полного AI-анализа качественных сцен действительно меньше ориентира.
- Если Ollama остаётся недоступен после bounded recovery, пользователь должен устранить сбой и повторно запустить анализ; готовые checkpoints сохраняются.
- Архив отчёта поддержки не содержит тяжёлые checkpoint-каталоги, но в исходной папке проекта они сохраняются штатно.
