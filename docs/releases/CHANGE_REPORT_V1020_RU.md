# Highlight Studio v10.2.0 — Modular Architecture

## Что изменено

- Backend переведён на слои `api`, `core`, `services`, `integrations`, `infrastructure`.
- Старые импорты сохранены compatibility-модулями.
- Frontend разложен на `app`, `api`, `components`, `config`, `features`, `shared`, `styles`.
- API client, форматирование, UI-компоненты, подсказки и task presets вынесены из `App.jsx`.
- Все Windows-скрипты собраны в `scripts/windows`.
- Сторонние exe и лицензии перенесены из `external_tools` в `vendor`.
- История версий убрана из корня в `docs/changelog`.
- Release builder перенесён в `tools/release` и проверяет обязательные файлы перед архивированием.
- Встроенный Release Audit понимает новую архитектуру.

## Совместимость

`START_HERE.bat` остаётся единственной основной точкой входа. Старые Python-импорты `backend.main`, `backend.pipeline`, `backend.utils` и другие сохранены.
