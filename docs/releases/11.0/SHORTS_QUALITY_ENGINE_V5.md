# Highlight Studio V5 — Shorts Quality Engine

This release upgrades Shorts from a vertical export of final montage segments into a separate quality pipeline.

## Implemented
- Candidate pool from micro, block, general and final segment artifacts.
- Shorts-specific hook/funny/reaction/surprise/payoff/standalone/energy scoring.
- Semantic rejection for waiting/reconnect/intermission/replay/prerecorded/advertisement.
- Boundary optimizer for setup → payoff → reaction.
- Optional bounded Ollama reranking with deterministic fallback.
- HIGH caption mode re-transcribes only selected Shorts with faster-whisper Small, beam 5.
- MAX caption mode attempts optional WhisperX alignment and falls back safely.
- Recognition dictionary/hotwords.
- 72px default ASS captions, 82px hook, 4-word caption beats and larger safe-zone margins.
- New Auto/Smart Face/Gameplay+Facecam UI modes with safe rendering fallback when optional tracking is not installed.
- Fingerprinted short-ASR cache; changing caption styling does not invalidate the expensive ASR cache.
- Shorts quality plan (`shorts_quality_plan.json`) with score explanations and rejection reasons.
- Optional component readiness helper for faster-whisper, WhisperX, SenseVoice/FunASR and MediaPipe.

## Reliability
Optional enhancement failure is degraded, not fatal. The core Shorts render continues with existing transcript and proven FFmpeg reframing.
