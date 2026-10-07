# Highlight Studio 10.15.4 — отчёт о функциональном remediation после QA-аудита 10.15.3

Дата: 2026-08-15

## 1. Executive Summary

Версия 10.15.4 является отдельной исправляющей сборкой поверх 10.15.3. Основная цель — устранить silent-correctness ошибки из QA-аудита: stale source/cache/results, неверную идентичность candidates, stale render, неполное покрытие длинного VOD, неоднозначную отмену jobs, слабые preflight gates, RBAC/SSRF риски и проблемы release identity.

Ключевое архитектурное изменение — введена единая revision/freshness модель:

`source_revision → analysis_revision → segments_revision → render_revision → publishability`

Теперь наличие файла на диске само по себе не означает, что он относится к текущему состоянию проекта. Анализ, монтаж и рендер проверяются на соответствие текущей ревизии.

### Release verdict

**READY WITH MINOR ISSUES**

Почему не `READY`: остаются области, которые нельзя полностью доказать в текущем Linux/CI окружении без внешних runtime-компонентов и реальных сервисов: Windows hard-crash/orphan-process reconciliation, реальный YouTube final-chunk crash recovery, live Ollama cancellation, полный browser/jsdom integration suite и Whisper/Silero VAD runtime. Кроме того, HS-021 и HS-034 закрыты не на 100% архитектурно: web concurrency исправлена, но полноценного fairness scheduler нет; критичные project-scoped frontend paths защищены generation guard, но не каждый вспомогательный async path имеет AbortController.

---

## 2. Что изменено по подсистемам

### Data integrity / revisions

Добавлены `core/revisions.py`, `core/artifacts.py` и `services/gates.py`.

- source revision использует сильную identity исходника;
- analysis revision учитывает source и analysis-sensitive settings;
- segment edits меняют segments revision;
- render привязан к source + segments + render settings;
- stale render не считается current и не предлагается YouTube Publisher;
- legacy-проекты без revision metadata мигрируют консервативно: неизвестные старые производные артефакты не объявляются current без доказательства совместимости.

### Twitch

- каждый downloader attempt изолирован в собственной директории;
- текущий attempt возвращает только свой output, а не «самый большой видеофайл в cache»;
- user cancellation не запускает fallback engine;
- range/source identity входит в revision/fingerprint.

### Transcription

- partial chunks теперь живут внутри fingerprint-generation;
- fingerprint включает source revision, Whisper model/language/chunk/preprocessing settings;
- stale chunks от прошлой генерации игнорируются;
- lifecycle `audio_16k.wav` продлён до завершения audio dynamics/events.

### Visual / OCR / audio

- длинный VOD равномерно покрывается `max_samples` по всей длительности;
- OCR preprocess key зависит от ROI/upscale/preprocess version;
- disabled visual scan возвращает explicit disabled state и не подхватывает старый enabled report;
- scene detection FFmpeg failure больше не кэшируется как успешные `0 scenes`;
- исправлен music/speech heuristic с реальными dBFS диапазонами.

### Candidates / Review / segments

- candidate identity стала immutable (`candidate_id`), display order отделён от identity;
- overlap/dedup/refill больше не ломают ID;
- `decision=remove/reject` не может попасть в финал только ради заполнения target duration;
- Review mutations проходят центральный segment validation;
- segment PUT использует optimistic concurrency revision; stale update получает `409 Conflict`, отсутствующая expected revision — `428` при существующем монтаже.

### Render / Render Factory

- обязательный backend pre-render gate;
- render пишет сначала временный output, валидирует его и только после успешного result check атомарно заменяет current final;
- validation failure не может стать `done`;
- старый валидный render сохраняется как historical artifact, но становится stale после изменения segments/settings;
- Render Factory больше не подменяет authoritative `segments.json` и не изменяет current render revision;
- factory outputs помечаются derivative и publishable только пока их base segments revision совпадает с current.

### Jobs / cancellation / recovery

- введён единый `OperationCancelled` contract;
- cancel не превращается в generic engine failure;
- `cancel_requested` не показывает 100%;
- long subprocess сохраняет owned-process metadata;
- startup reconciliation сверяет PID + process start marker + project command identity перед завершением orphan child;
- resume стал stage-aware и учитывает revision compatibility;
- web concurrency больше не жёстко равна одному job: configurable limit, desktop default 1, web default 4, one-job-per-project сохраняется.

### Ollama / security

- web-mode использует server-managed Ollama endpoint и не принимает arbitrary project host;
- desktop mode остаётся локально настраиваемым;
- streamed Ollama transport проверяет cancellation между chunks/lines и использует bounded transport operations;
- canonical project-id validation едина для router и RBAC middleware;
- OAuth callback errors HTML-escape;
- существующие CSRF/HttpOnly/contextIsolation/nodeIntegration=false сохранены.

### YouTube

- durable upload state сохраняет intent, session URL, file identity, render revision, offset/status, known video id;
- при restart существующая session проверяется до создания новой;
- ambiguous final-chunk state не создаёт новый upload автоматически;
- stale render блокируется;
- upload разбит на меньшие bounded chunks с cancel checks;
- partial batch failure больше не становится обычным `done`.

### Frontend

- release assets имеют уникальную 10.15.4 identity;
- critical project-scoped async paths используют project-generation guard;
- segment writes передают revision и обрабатывают 409/428;
- stale render исключён из YouTube selection;
- `smart_zoom` больше не обещает tracking: интерфейс честно описывает center crop без слежения;
- UI geometry из 10.15.3 сохранена, косметический redesign не делался.

### Release engineering

- canonical `release_identity.json` используется backend/Electron/release tools;
- Electron/Tauri identity обновлена на 10.15.4;
- CI проверяет актуальный generated archive, а не старое hardcoded имя;
- release builder/verifier требуют `release_identity.json` и текущие 10.15.4 contract/report files.

---

## 3. Self-audit HS-001…HS-042

| ID | Итог | Проверка / комментарий |
|---|---|---|
| HS-001 | **FIXED** | Isolated Twitch attempt; regression old 5000 B vs current 2000 B. |
| HS-002 | **FIXED** | Audio temp artifact удаляется после consumers; regression lifecycle. |
| HS-003 | **FIXED** | Transcript chunks generation-scoped by fingerprint; crash/stale generation test. |
| HS-004 | **FIXED** | Invalid result check raises; failed rerender cannot replace last valid final. |
| HS-005 | **FIXED** | Canonical release identity used by backend/Electron/Tauri; Electron tests pass. |
| HS-006 | **FIXED** | Canonical project ID validator + legacy `_`/`-` RBAC regression. |
| HS-007 | **FIXED** | `run_cmd()` raises cancellation semantics instead of treating SIGTERM as generic failure. |
| HS-008 | **FIXED** | Twitch user cancellation bypasses fallback; regression passes. |
| HS-009 | **FIXED** | Import boundary lightweight ffprobe/media validation rejects corrupt/0-byte video. |
| HS-010 | **FIXED** | Central recomputable cache registry; clear-project-cache regression. |
| HS-011 | **FIXED** | Incompatible cache is not restamped before recomputation; old helper leakage fixed. |
| HS-012 | **FIXED** | OCR preprocess fingerprint changes with ROI/upscale/preprocess version. |
| HS-013 | **FIXED** | visual disabled returns current disabled state, not stale enabled report. |
| HS-014 | **FIXED** | Scene FFmpeg failure is explicit failure/degradation, not valid cached zero scenes. |
| HS-015 | **FIXED** | Review mutations use central segment validation path. |
| HS-016 | **FIXED** | `/render` enforces authoritative backend gate. |
| HS-017 | **FIXED** | YouTube upload uses 2 MiB bounded chunks and checks cancellation between requests/retries. |
| HS-018 | **NEEDS EXTERNAL RUNTIME VALIDATION** | Durable exactly-once state implemented and fake/restart regressions pass; real YouTube final-chunk hard-crash requires live external validation. |
| HS-019 | **FIXED** | Partial upload batch is error/partial_failed, never normal done. |
| HS-020 | **NEEDS EXTERNAL RUNTIME VALIDATION** | PID/start-marker/project identity reconciliation tested with fakes; real Windows hard-kill/orphan behaviour still needs Windows probe. |
| HS-021 | **PARTIALLY FIXED** | Global hardcoded concurrency=1 removed; web default 4 + configurable limits + one-job-per-project. Full per-user fairness scheduler not implemented. |
| HS-022 | **FIXED** | Resume is stage-aware and revision-aware. |
| HS-023 | **FIXED** | Checkpoints use central actual artifact names. |
| HS-024 | **FIXED** | cancel_requested preserves progress; does not claim 100%. |
| HS-025 | **FIXED** | OAuth callback error is HTML-escaped. |
| HS-026 | **FIXED** | Source identity uses size+mtime_ns+partial SHA-256 head/middle/tail. |
| HS-027 | **FIXED** | Candidate identity immutable; overlap/refill regression passes. |
| HS-028 | **FIXED** | Visual/OCR producer/consumer artifact names centralized. |
| HS-029 | **FIXED** | Auto Video Probe reads actual OCR report contract. |
| HS-030 | **FIXED** | Missing Fast Import external source becomes not-ready; analyze/render blocked. |
| HS-031 | **FIXED** | Render revision stale after montage/settings change; stale output not publishable. |
| HS-032 | **FIXED** | Analysis revision invalidated by source/AI/prompt/preset/target/transcription/OCR/visual/audio/profile settings. |
| HS-033 | **FIXED** | Fatal preflight always blocks direct analyze regardless `strict_preflight`. |
| HS-034 | **PARTIALLY FIXED** | Critical paths (`loadProjectById`, refresh, settings, autopilot, segment writes) use project generation guard; preview has cancellation cleanup. Exhaustive browser timing validation of every auxiliary async action is still required. |
| HS-035 | **FIXED** | Optimistic segment concurrency with 409/428, frontend refresh/conflict handling. |
| HS-036 | **NEEDS EXTERNAL RUNTIME VALIDATION** | Streamed Ollama cancellation contract is test-covered; real long-running Ollama model cancellation needs live Ollama runtime. |
| HS-037 | **FIXED** | Web project cannot redirect Ollama to arbitrary host; server-managed endpoint. |
| HS-038 | **FIXED** | Fill-target excludes remove/reject; quality can remain below target instead of adding rejected content. |
| HS-039 | **FIXED** | Uniform visual sampling across 6h VOD verified start/middle/end. |
| HS-040 | **FIXED** | Product truthfulness route: mode is explicitly center crop without tracking; no heavy dependency added. |
| HS-041 | **FIXED** | Impossible `p50 > 0 dBFS` heuristic replaced and tested on music/speech/silence/loud profiles. |
| HS-042 | **FIXED** | CI/release archive path is generated/current 10.15.4, not stale v1050 name. |

### Totals

- FIXED: **37**
- PARTIALLY FIXED: **2** (HS-021, HS-034)
- NEEDS EXTERNAL RUNTIME VALIDATION: **3** (HS-018, HS-020, HS-036)
- NOT FIXED: **0**
- NOT REPRODUCIBLE: **0**

> Примечание: HS-018/020/036 имеют реализованное исправление и regression coverage; статус оставлен `NEEDS EXTERNAL RUNTIME VALIDATION`, потому что внешний сервис/Windows hard-crash нельзя честно воспроизвести в текущем Linux-контейнере.

---

## 4. Новые дефекты, найденные во время remediation

| ID | Статус | Проблема | Исправление |
|---|---|---|---|
| HS-NEW-001 | **FIXED** | Startup orphan reconciliation возвращал `int`, а lifespan трактовал результат как dict. | Уточнён contract startup reconciliation и добавлены regression probes. |
| HS-NEW-002 | **FIXED** | Canonical render мог перезаписать последний валидный final до post-render validation. | Temp render → validate → atomic replace. |
| HS-NEW-003 | **FIXED** | Первая версия analysis revision не включала часть selection/visual settings. | Расширен canonical `ANALYSIS_SETTING_KEYS`. |
| HS-NEW-004 | **FIXED** | Recovery status мог обходить authoritative source/render freshness. | Recovery использует source readiness и revision freshness. |
| HS-NEW-005 | **FIXED** | Render Factory временно подменял `segments.json` и мог изменять authoritative render/revision state. | Factory render использует `segments_override`, derivative output и `authoritative=False`; canonical segments не переписываются. |

---

## 5. Основные изменённые файлы

### Новые core/domain модули

- `backend/src/highlight_studio/core/artifacts.py` — artifact registry, media/source identity, source readiness, cache registry.
- `backend/src/highlight_studio/core/revisions.py` — source/analysis/segments/render revision и publishability.
- `backend/src/highlight_studio/services/gates.py` — authoritative analyze/render/source gates.
- `backend/src/highlight_studio/tests` не создавался; regression tests находятся в root `tests/` по существующей структуре.

### Backend

- `backend/src/highlight_studio/api/app.py` — API gates, revisions, cache clearing, RBAC project id, segments OCC, recovery/resume, YouTube/render/Render Factory contract.
- `backend/src/highlight_studio/services/pipeline.py` — sampling, candidate identity, transcript generations, render temp validation, non-authoritative factory render, cache semantics.
- `backend/src/highlight_studio/core/settings.py` — 10.15.4 identity/concurrency/canonical settings.
- `backend/src/highlight_studio/core/utils.py` — cancellation/process ownership helpers.
- `backend/src/highlight_studio/integrations/twitch/source.py` — isolated attempts and cancel handling.
- `backend/src/highlight_studio/integrations/ai/client.py` — cancellation-aware AI orchestration.
- `backend/src/highlight_studio/integrations/ai/ollama.py` — streamed/bounded/cancellable transport.
- `backend/src/highlight_studio/integrations/youtube/publisher.py` — durable resumable upload state/exactly-once safeguards.

### Frontend

- `frontend/src/app/App.jsx` — project generation guards, segments revision conflicts, stale render filtering, truthful Shorts label.
- `frontend/dist/...10154...` — release-specific production asset identity.
- `frontend/tests/remediation10154.contract.test.js` — frontend remediation contracts.
- `frontend/tests/workspaceV154.contract.test.js` — current release workspace contract.

### Desktop / release

- `release_identity.json` — canonical release identity.
- `desktop/electron/main.js`, `package*.json`, security tests — release identity and shell contract.
- `desktop/tauri/src-tauri/tauri.conf.json` — version identity.
- `tools/release/_identity.py`, `make_release.py`, `verify_archive.py` — canonical release/root/archive verification.
- `.github/workflows/ci.yml` — current generated archive verification.
- `CHECK_V10154.bat`, `BUILD_INFO_V10154_RU.txt` — Windows release checks.

---

## 6. Новые regression tests

Главный новый suite:

`tests/test_functional_remediation_10154.py`

Он содержит **51 passing tests**, включая:

1. source/analysis/render revision roundtrip;
2. stale analysis after settings change;
3. stale render after segments mutation;
4. Twitch current attempt vs larger stale output;
5. stale transcript chunks;
6. candidate immutable identity/refill;
7. failed render validation;
8. failed rerender preserves last valid final;
9. 6-hour visual sampling;
10. missing external source readiness;
11. Electron release identity;
12. legacy project ID RBAC;
13. Ollama SSRF;
14. YouTube durable session recovery/ambiguous session protection;
15. subprocess cancellation;
16. Twitch cancel no fallback;
17. clear-project/incompatible cache;
18. OCR preprocess invalidation;
19. visual disabled freshness;
20. scene FFmpeg failure;
21. Review central validation;
22. render hard gate;
23. YouTube partial failure;
24. checkpoint producer/consumer artifact names;
25. cancel progress semantics;
26. OAuth HTML escaping;
27. strong source signature;
28. auto-probe OCR filename;
29. analyze fatal preflight;
30. segment stale revision 409;
31. Ollama cancellation contract;
32. fill-target remove exclusion;
33. smart-zoom label truthfulness;
34. CI archive path;
35. corrupt MP4 import rejection;
36. stage-aware resume;
37. web concurrency policy;
38. audio artifact lifecycle;
39. music heuristic;
40. orphan process PID identity/reuse;
41. YouTube chunk cancellation;
42. recovery authoritative freshness;
43. Render Factory non-authoritative behavior and stale derivative publishability.

Frontend contract coverage дополнительно проверяет generation guards, optimistic segment revisions, stale render filtering и production identity.

---

## 7. Результаты test suite

### Python full suite

Команда:

`PYTHONPATH=.:backend/src python -m pytest -q`

Результат:

**243 passed / 2 failed**

Оба failure — environment/runtime readiness tests:

- `test_system_check_endpoint_returns_readiness`
- `test_runtime_components_include_working_whisper_vad`

Фактическая причина в текущем Linux-окружении:

`Whisper Silero VAD: ok=false, hint="No module named 'onnxruntime'"`

Это не скрыто и не переопределено ради зелёного suite.

### Functional remediation suite

`PYTHONPATH=.:backend/src python -m pytest -q tests/test_functional_remediation_10154.py`

**51 passed / 0 failed**

### Frontend

Полная команда:

`node --test frontend/tests/*.test.js`

**53 passed / 2 failed**

Оба failed test files не загружаются, потому что в текущем release/test environment отсутствует штатная dev dependency `jsdom`:

- `frontend/tests/app.integration.test.js`
- `frontend/tests/onboarding.test.js`

Все frontend tests, не требующие jsdom:

**53 passed / 0 failed**

Сетевые установки npm dependencies намеренно не выполнялись повторно после того, как предыдущая попытка нарушила рабочее окружение. Это отмечено как limitation, а не спрятано.

### Electron

`node --test desktop/electron/tests/*.test.js`

**10 passed / 0 failed**

### Compile

`python -m compileall -q backend tests`

**PASS**

### Ruff/static lint

`python -m ruff check backend tests`

**NOT RUN / dependency unavailable**: `No module named ruff`.

### Release identity

`PYTHONPATH=.:backend/src python tools/diagnostics/verify_release_identity.py`

**PASS** — `Highlight_Studio_10.15.4 / v10.15.4-functional-remediation / studio-audited-v11`.

---

## 8. Что не удалось полностью проверить

1. Реальный Twitch subscriber/private VOD и сетевые interruptions на Windows.
2. Реальный многочасовой 6h VOD end-to-end; uniform timestamp planner проверен синтетически.
3. Real Whisper/Silero VAD runtime в текущем Linux environment — отсутствует `onnxruntime`/runtime assets.
4. Hard-kill Windows backend с реально оставшимся FFmpeg/Twitch child; ownership reconciliation проверен моделями PID/start-marker.
5. Реальный Ollama long-generation cancel; transport/callback contract покрыт тестом.
6. Реальный YouTube OAuth + final-chunk accepted/response lost/backend crash; durable state machine проверена fake-session тестами.
7. Два frontend integration files на jsdom, поскольку dev dependency недоступна в текущем environment.
8. Полноценный multi-user fairness load test для web concurrency.
9. Exhaustive timing test каждого вспомогательного project-scoped frontend request при A→B→A switching.

---

## 9. Остаточные риски

### HS-021: web fairness

Global `max_concurrent=1` устранён. Однако система всё ещё не является полноценным fair scheduler по пользователям. Для локального desktop это осознанно не нужно; для крупного web deployment желательно добавить per-user quota/weighted fairness.

### HS-034: frontend auxiliary async requests

Основные state-changing paths защищены generation guards, preview использует cleanup, segment writes — OCC. Но в `App.jsx` остаются вспомогательные project-bound actions, которые после ответа могут показать notice/report старого проекта. Они не должны повреждать backend data благодаря project-bound API и revision gates, но могут создать временно неправильный UI notification/state. Для полного доказательства нужен browser timing suite с jsdom/Playwright.

### External integrations

YouTube/Twitch/Ollama behavior зависит от внешних сервисов, реальных credentials и Windows networking. Кодовые контракты и fake regressions проходят, но production rollout всё равно должен пройти canary/manual validation.

---

## 10. Data-safety вывод

После remediation основные derived artifacts теперь привязаны к revision и не считаются current только по факту существования. Legacy/historical output не уничтожается автоматически. Source, manually edited segments, render outputs, YouTube metadata и user settings не удаляются при freshness migration без необходимости.

Ключевой эффект: после изменения анализа или монтажа приложение должно показывать старый результат как **stale**, а не молча использовать его как текущий.

---

## 11. Release recommendation

### Обычный desktop-пользователь

**Можно выпускать ограниченным стабильным/canary релизом**, при условии Windows smoke-test на машине с реальными FFmpeg/Whisper/Ollama runtime компонентами.

### Платный массовый релиз

Рекомендуется после короткого external validation gate:

- Windows VAD readiness;
- 1 реальный многочасовой Twitch VOD;
- cancel Twitch/render;
- real Ollama cancel;
- YouTube test-channel crash/recovery probe.

### Итог

**READY WITH MINOR ISSUES**

Это не означает «идеально» или «всё доказано». Это означает, что подтверждённые silent-correctness P1/P2/P3 причины из QA-аудита устранены/закрыты regression tests, а оставшиеся оговорки относятся преимущественно к external runtime validation и двум не полностью исчерпанным архитектурным рискам (fairness и exhaustive frontend async timing).
