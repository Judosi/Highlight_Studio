# Highlight Studio 10.15.20 — Robust AI/Resume V4

This build integrates the reliability fixes as one release rather than a chain of user-applied hotfixes.

## Long-analysis reliability

- Block AI and Micro AI no longer fail an ordinary analysis solely because one local Ollama request stalls or returns incomplete JSON.
- Strict AI / Full AI Coverage remain fail-closed.
- Successful Block AI results are cached per block (`ai_items`) instead of only by batch number.
- Successful Micro AI results are cached per micro window (`micro_items`) instead of only by batch number.
- Existing 10.15.20 whole-batch checkpoints are still read and promoted to the new per-item cache.
- Degraded placeholders are never written into authoritative AI cache, so a healthy later run can replace only the missing inference.
- A circuit breaker prevents dozens of repeated 45-second waits after a confirmed Ollama runtime failure.
- Normal-mode degraded fallback is quality-bounded and preserves the semantic non-primary-content guard.
- If the regular quality selector would otherwise produce zero clips after an AI outage, a narrow deterministic minimum-montage recovery may select only safe borderline candidates; explicit rejects and high-confidence replay/intermission/advertisement content remain forbidden.

## Ollama runtime

- Structured JSON calls disable model thinking by default (`ollama_think=false`) to avoid spending most of the budget in hidden reasoning on Qwen3-class models. It can be explicitly enabled in settings.
- The 360-second base hard deadline now applies to requests that have not become useful. Once a stream is actively returning response text, it may continue to the bounded active deadline (`ai_request_active_hard_timeout`, default 900 seconds, never beyond the caller timeout).
- A true socket/data stall is still detected independently by `ai_stream_stall_timeout` (default 45 seconds).
- Failed warmups use bounded recovery/unload/reload before degraded mode.
- Dedup and Storyline skip additional AI retry storms after a failed warmup in normal mode and use deterministic safety guards instead.
- Vision AI is skipped after bounded failed recovery in normal mode; scene detection/OCR/audio analysis remain available.

## Preflight and cache portability

- Temporary Ollama unavailability is a WARN in ordinary analysis because the pipeline has a safe degraded path. It remains a FAIL in Strict/Full-AI mode.
- Transcript and scene-detection cache identity is content-based (size + distributed partial SHA-256) rather than path/mtime-based, so moving a portable project does not invalidate hours of analysis.
- Legacy 10.15.20 transcript/scene fingerprints are migrated without recomputation when they still validate.

## CUDA / Whisper

Includes the earlier integrated CUDA readiness remediation: CTranslate2/CUDA is considered usable only when the required runtime DLLs can actually be loaded. Otherwise Whisper selects the safe CPU fallback before a long job rather than failing on the first chunk.

## Job and render correctness

- `cancelled` jobs retain their real progress; only `done` means 100%.
- Authoritative render jobs pin the exact source/analysis/segments/render generation they consume.
- If segments/source/render settings change while FFmpeg is running, the produced video is saved as a historical `.stale_...` output and is **not** committed over the current canonical final or marked as the newer revision.
- `analysis_health.json` records whether an analysis completed normally or with bounded degraded recovery.

## Regression coverage added

Coverage includes active Ollama streams beyond the base hard deadline, per-block/per-window cache identity, cache-poison prevention, normal-vs-strict AI preflight behavior, minimum degraded selection safety, cancellation progress, source-path-independent analysis fingerprints, and mid-render generation mutation.
