# Highlight Studio v8.8.0 — Pro Stability + IRL Visual/OCR + Product Tools

## P0 stability
- AI Metadata / AI Metadata 100% now run as background jobs instead of blocking the HTTP request.
- Added SQLite job history (`.highlight_studio/jobs.sqlite3`) with latest job in `/status` and `/api/jobs` endpoints.
- Added local auth token. Frontend obtains it from `/api/health` and sends `X-Local-Token`; file/video URLs use `?local_token=`.
- Added subprocess registry and cancel kills registered FFmpeg/Tesseract subprocesses.
- Status now supports stage/batch progress and ETA fields (`stage`, `current_batch`, `total_batches`, `eta_seconds`).
- Upload flow blocks huge files >2 GB and tells the user to use Fast Import without copying.
- Frontend and backend dependency versions are pinned; no `latest` in `package.json`.

## P1 montage quality
- Added lightweight whole-video visual scan (`visual_scan_report.json`) sampling frames every N seconds.
- Added OCR scan for chat/donations/screen text via Tesseract if installed (`ocr_scan_report.json`).
- Added IRL audio event detector (`audio_events.json`) for peaks, sudden reactions and silence.
- Expanded AI JSON schema: `decision`, `hook_potential`, `context_before_seconds`, `context_after_seconds`, `moment_type`, `standalone_clarity`.
- Added personal editing profile from feedback (`editing_profile.json`) and scoring calibration based on liked/disliked examples.
- Added separate IRL pipeline pass combining text keywords, visual scan, OCR and audio events.
- Added hook-first story ordering: a strong hook can be rendered first, then body continues chronologically.

## P2 product layer
- Added desktop packaging scaffolds: `desktop/electron` and `desktop/tauri`.
- Added onboarding bar with presets: YouTube 15, YouTube 30, Shorts pack, IRL story, IRL chaos.
- Added HLS/proxy preview endpoint/button.
- Added presets and product buttons for Visual scan, OCR, audio events, timeline export, thumbnail ideas and Compare A/B.
- Added EDL + FCPXML timeline export for Premiere/DaVinci/Final Cut style workflows.
- Added thumbnail idea generator using best moments and extracted frames.
- Added Compare mode: A dense cut vs B story cut.

## Notes / limitations
- OCR requires a local Tesseract installation. Without it, the app writes a clear report explaining that OCR is unavailable.
- Visual scan is a lightweight full-video sampler. It does not mean a vision LLM watched every frame; it prepares frames and metadata for IRL/OCR/thumbnail workflows.
- Electron/Tauri are scaffolds for desktop packaging. Full installer builds require Node/Rust tooling on your PC.
- SQLite jobs persist history and interrupted states, but full resume-after-crash for Whisper/LLM batches is still based on existing cache checkpoints.
