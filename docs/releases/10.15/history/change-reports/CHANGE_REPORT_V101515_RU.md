# Highlight Studio 10.15.15 — Change Report

## Цель

10.15.15 — стабилизационный релиз после технического аудита и AI Reliability 10.15.14. Качество отбора, Qwen3:8b и visual/OCR coverage намеренно не упрощались.

## Исправлено

### Production frontend state

- удалён старый `frontend/dist/ux-workflow-101513.js`, который самостоятельно перехватывал клики, polling, navigation и reload;
- production workflow теперь принадлежит React bundle;
- `ui-presentation-101515.js` ограничен визуальными переименованиями и не содержит API, polling, reload, storage-state или click interception;
- terminal `one_click/analysis = done` гидратирует полный dashboard и разблокирует Монтаж без принудительной навигации;
- Task Center использует единый jobs runtime и показывает фоновые задачи.

### Новый проект

- «Новый проект» сбрасывает project-scoped UI state;
- старые candidates/segments/status/preview/errors не должны просачиваться в draft;
- выбранный старый project ID удаляется до создания нового source;
- late async responses старого проекта инвалидируются project scope generation.

### Review / Montage

- готовая AI-нарезка открывается первой, если segments уже существуют;
- кандидаты называются «Альтернативы»;
- атомарный `/segments/add` и optimistic revision guard сохранены;
- stale response другого проекта не записывает montage в текущий UI.

### Async safety

Дополнительные scope guards добавлены для project-scoped diagnostics/actions и clip preview. Preview использует AbortController.

## Сохранено из 10.15.14

- Qwen3:8b;
- Semantic Quality и temporal fairness;
- технический/replay guard;
- AI batch schema/completeness validation;
- targeted rescue вместо многократного full-batch inference;
- AI hard deadline / stall timeout;
- Auto Hardware capability policy;
- render/data-integrity fixes предыдущих remediation фаз.

## Не заявляется как полностью закрыто

Clean `npm ci -> Vite build -> lint` невозможно воспроизвести в текущей QA-среде: npm package payloads отсутствуют, внешний network отключён. Поэтому build assurance остаётся отдельным P2 ограничением. Исполняемый production bundle проверяется Node syntax, contract tests и headless Chromium regression tests.
