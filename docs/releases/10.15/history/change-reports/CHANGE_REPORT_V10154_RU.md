# Highlight Studio 10.15.4 — Functional Remediation

Версия 10.15.4 — отдельная исправляющая сборка после функционального QA-аудита 10.15.3.

## Главное

- введена revision/freshness модель `source → analysis → segments → render → publish`;
- устранено использование stale Twitch outputs и stale transcript chunks;
- candidate identity сделана immutable;
- long-VOD visual sampling равномерно покрывает весь VOD;
- stale analysis/render больше не считаются current;
- render валидируется до атомарной замены финального файла;
- Render Factory больше не изменяет authoritative montage/render state;
- Fast Import source readiness проверяет реальный файл/media;
- cache artifact contracts централизованы;
- cancellation semantics унифицированы;
- YouTube upload получил durable resumable state и защиту от автоматического duplicate retry в ambiguous final state;
- web Ollama endpoint защищён от project-level SSRF;
- RBAC project-id validation унифицирована;
- segment updates используют optimistic concurrency (409/428);
- Electron/Tauri/backend/release identity приведены к 10.15.4;
- CI/release scripts проверяют фактически создаваемый архив.

## Проверки

- Functional remediation: **51 passed / 0 failed**.
- Python full: **243 passed / 2 failed**; оба failure связаны с отсутствующим в текущем Linux environment Whisper/Silero VAD runtime (`onnxruntime`).
- Frontend без jsdom-dependent файлов: **53 passed / 0 failed**.
- Full frontend invocation: **53 passed / 2 failed**, оба test files не загружаются из-за отсутствующего `jsdom` dev dependency.
- Electron: **10 passed / 0 failed**.
- `compileall`: PASS.
- Release identity: PASS.

Полная таблица HS-001…HS-042 и остаточные риски: `FUNCTIONAL_REMEDIATION_10.15.4_RU.md`.
