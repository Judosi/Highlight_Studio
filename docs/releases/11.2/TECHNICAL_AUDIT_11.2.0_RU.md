# Технический аудит Highlight Studio 11.2.0

## Итог

Аудит подтвердил четыре риска, которые проявляются не на happy path, а при вложенных AI-вызовах, конкурентном запуске, копировании проекта и частичном восстановлении Shorts. Все четыре закрыты проверяемыми контрактами без изменения алгоритмов творческого отбора.

| Риск | Исправление | Гарантия |
|---|---|---|
| Self-deadlock при `gpu_job_limit=1` | thread-reentrant `ResourceManager` | вложенный вызов имеет depth 2, но active остаётся 1 |
| Правка Short сохраняется без запущенной job | admission callback под lifecycle/jobs locks | нет мутации при занятом проекте или ошибке prepare |
| Копия проекта читает старую папку | relative-first source resolver | локальный media-файл копии имеет приоритет |
| MP4 есть, строки manifest нет | синтез полной preserved row | путь и индекс не теряются после сбоя regeneration |

Дополнительно worker сам закрывает не-terminal status как успешный `done`, а небезопасные относительные source-пути не разрешаются вне project root.

## Совместимость

- Project schema остаётся `4`; миграция пользовательских проектов не требуется.
- Pipeline state schema остаётся `2`.
- Design identity остаётся `studio-audited-v15`: визуальный контракт не менялся.
- Абсолютные внешние reference-пути и optional Shorts dependencies (WhisperX, SenseVoice, MediaPipe) сохранены.

## Проверка

- Backend regression suite: **405 passed**.
- Frontend contract suite без двух jsdom-only файлов: **83 passed**. Два browser-emulation теста не запускались в audit-среде из-за недоступного npm-пакета `jsdom`; соответствующие source/production contracts покрыты остальным набором.
- Electron security suite: **10 passed**.
- Python `compileall` и release identity: пройдены.
- Итоговый ZIP дополнительно проверяется на единственный root, запрещённые runtime/secret-файлы и полное соответствие SHA-256 manifest.

## Остаточные ограничения

- Глобальный resource manager действует внутри одного backend-процесса; несколько независимых backend-инстансов требуют внешнего распределённого лимитера.
- Большие VOD используют распределённый частичный content hash, а не полное чтение всего многогигабайтного файла.
- Качество и скорость optional ML-модулей зависят от драйверов и доступной VRAM целевого Windows-компьютера.
