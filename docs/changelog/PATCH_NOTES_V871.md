# Patch notes v8.7.1 — Fast Import + IRL

## Fast Import для больших видео

Добавлен режим создания проекта по локальному пути без копирования исходника в папку проекта.

Новый endpoint:

```http
POST /api/projects/from-path
```

Payload:

```json
{
  "source_path": "D:/Streams/stream_12gb.mp4",
  "storage_mode": "reference"
}
```

Режимы:

- `reference` — самый быстрый режим: проект хранит путь к оригинальному файлу, копии 12 GB не создаются.
- `link` — пытается создать hardlink/symlink; если ОС не разрешила, автоматически падает обратно в `reference` без копирования.
- `copy` — старое поведение, файл копируется в проект.

Важно: в `reference` режиме нельзя перемещать или удалять исходное видео до завершения анализа/рендера.

## IRL-стримы

Добавлены:

- `content_type`: `IRL стрим`, `IRL прогулка/город`;
- A/B режимы: `IRL плотный`, `IRL история`, `IRL конфликт/хаос`;
- отдельный IRL prompt profile в backend;
- IRL bonus в ранжировании кандидатов;
- Auto-настройки для IRL: больше контекста, audio dynamics, storyline, micro-cut, dedup;
- кнопки IRL-пресетов в UI.

IRL-профиль ищет:

- прохожих, охрану, продавцов, случайных персонажей;
- конфликт, неловкость, хаос, резкие реакции;
- донат/чат, который меняет ситуацию;
- смену локации, события на улице, визуальные приколы;
- личные истории и мини-сцены с развитием.

## Проверки

```bash
python -m compileall backend tests
pytest -q
# 8 passed

npm --prefix frontend install
npm --prefix frontend run build
# build OK
```
