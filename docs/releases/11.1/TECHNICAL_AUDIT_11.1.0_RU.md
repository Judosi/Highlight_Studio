# Технический и продуктовый аудит Highlight Studio 11.1.0

## Вывод

Главный риск 11.0 был не в отдельном AI-промпте, а в управлении состоянием: параллельные GPU-потребители, абсолютные пути, разрозненные checkpoint-файлы и destructive Shorts export. В 11.1 эти риски закрыты общими инфраструктурными контрактами.

## Что исправлено

| Риск | Реализация 11.1 | Проверяемая гарантия |
|---|---|---|
| Потеря прогресса после закрытия | `DurablePipelineState` | running → recoverable partial/pending |
| OOM/VRAM contention | общий `ResourceManager` / `gpu_heavy` | Whisper, Ollama и render сериализуются по единому лимиту |
| Проект не открывается после переноса | portable source resolver + schema v3 | относительный fallback и content signature |
| Ollama долго не даёт первый токен | отдельный TTFT budget | loading не считается stream stall |
| Повтор бесполезных AI-запросов | circuit breaker | open после порога, half-open после cooldown |
| Один сбой уничтожает готовые Shorts | pending + validation + atomic replace | старый canonical сохраняется |
| Нельзя исправить один Short | per-candidate API/UI | partial regeneration по индексу |

## Остаточные ограничения

- Circuit breaker локален процессу и не распределён между несколькими backend-инстансами.
- Content signature больших VOD является распределённым частичным SHA-256, а не полным хешем всего многогигабайтного файла.
- Автоматическое face tracking остаётся best-effort и зависит от доступного runtime; безопасные crop/blur fallback сохранены.
- Реальная производительность и качество отбора должны дополнительно измеряться на целевых Windows-конфигурациях и длинных пользовательских VOD.

## Рекомендованный следующий этап

Разделить крупные `api/app.py` и `services/pipeline.py` на route/service-модули только после фиксации текущих contract-тестов. Это снизит blast radius изменений, но не должно смешиваться с изменением алгоритмов отбора моментов.
