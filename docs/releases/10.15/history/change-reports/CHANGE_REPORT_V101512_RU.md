# Highlight Studio 10.15.12 — Terminal State Sync & Review Stability

## Главная исправленная ошибка

В 10.15.11 могла возникнуть ситуация:

- backend/Task Center: `one_click = done`, 100%, найдено 57 моментов;
- основной экран Analysis: старое состояние «Проект готов / Начать анализ»;
- sidebar: `Найдено 0`;
- «Монтаж»: заблокирован как «После анализа».

Причина: source `App.jsx` уже содержал terminal full-refresh, но реальный production bundle не содержал этот эффект. Глобальный Task Center получал актуальный `/api/jobs`, а основной React оставался со старым dashboard snapshot.

## Исправление

1. В исходный React добавлена синхронизация по успешному terminal analysis job текущего проекта.
2. После `one_click / analysis / analyze / ai_analyze = done` выполняется один `refreshAll()` текущего проекта.
3. Production `ux-workflow-101512.js` имеет независимый watchdog:
   - проверяет `/api/jobs`;
   - сверяет `/api/projects/{id}/dashboard-state`;
   - если backend уже считает analysis current, а UI всё ещё показывает старый pre-analysis snapshot, выполняет ограниченную служебную перезагрузку;
   - максимум 2 попытки на один terminal job, чтобы исключить reload-loop.
4. Перед reload сохраняется `highlightStudioLastStep=analysis`, поэтому пользователь остаётся на Analysis и сам решает, когда открыть «Монтаж».
5. После актуальной гидратации `analysis_current`, candidates и segments разблокируют Review/Монтаж даже при ручной навигации.

## Что сохранено из 10.15.11

- атомарный `POST /segments/add` для «Добавить в итоговую нарезку»;
- защита от Firefox `can't access lexical declaration ... before initialization` на старом add path;
- result-first Review: «Итоговая нарезка» + «Альтернативы»;
- глобальный Task Center;
- понятные названия pipeline-этапов;
- «Ориентир по длительности» вместо требования добить ровно 30/40 минут;
- предупреждение перед повторным анализом;
- ручные переходы между крупными этапами.

## Что намеренно не менялось

- Qwen3 8B;
- Semantic Quality / reconnect-replay guards;
- AI Runtime;
- scoring/selection;
- Visual/OCR coverage;
- hardware optimizer;
- render logic.

## Регрессионные проверки

- stale terminal state: `done + 57 candidates` при DOM `НАЙДЕНО 0 / Начать анализ / Монтаж после анализа` → watchdog срабатывает, manual Analysis step сохраняется;
- Review add production Chromium: `/segments/add` вызывается, page errors = 0;
- полный backend test corpus: 295 PASS, 2 FAIL только в Linux QA из-за отсутствующего `onnxruntime` для Whisper Silero VAD; Windows requirements содержат `onnxruntime==1.28.0`;
- frontend contract tests без двух jsdom-dependent suites: 76/76 PASS;
- Electron security/release tests: 10/10 PASS;
- production Review add Chromium: page errors = 0;
- production terminal-sync Chromium: stale `done + 57 candidates` сценарий распознан, manual Analysis step сохранён.
