# Shorts Studio Implementation Plan

> **For agentic workers:** Use executing-plans for the frontend and an independent backend worker; user already requested implementation and delivery.

**Goal:** Convenient working Shorts editor, ready as a full ZIP.
**Architecture:** React master-detail workspace; existing project APIs plus save-one endpoint; stable candidate indices through render. Parent owns frontend, independent worker owns backend and Python tests.
**Tech Stack:** Existing React 18 / Vite / FastAPI / Python / FFmpeg.
**Spec:** docs/superpowers/specs/2026-09-15-shorts-studio.md

## Global Constraints
- No new runtime dependencies. Preserve Windows launchers/vendor assets and previous hotfixes.
- Candidate API index stays 1-based. caption_text null selects automatic mode; string selects manual mode.
- Deliver rebuilt frontend/dist inside full app ZIP.

## Task 1 — Backend identity and saved edits
- [x] Add failing API tests for PUT /projects/{id}/shorts/{index}, busy rejection and invalid times.
- [x] Validate/persist candidate and factory manifest under existing locks; return {ok:true,candidate}.
- [x] Add regression for out-of-order strengths and only_indexes outside requested count.
- [x] Fix render source identity, bypass reselection for individual render, mark stale artifacts.
- [x] Run targeted Python regressions.

## Task 2 — React workspace
Files: frontend/src/features/shorts/{ShortsStudio.jsx,shorts.js,shorts.css}; App.jsx; index.html; tests/shortsStudio.test.js.
- [x] Write tests for parseShortTime('2:43:23,56') === 9803.56, invalid input and payload auto/manual captions.
- [x] Implement pure helpers plus component with local per-project draft recovery, search, clip selector and focused editor.
- [x] Connect PUT save and POST render with scoped callbacks. Await saving settings before render. Reject started:false; prevent duplicate submits.
- [x] Replace App export subsection; remove legacy script inclusion; status/settings use supplied React props/children.
- [x] Test real component selection, errors, persistence and successful save/render.

## Task 3 — Validation and delivery
- [x] npm run build; node --test tests/shortsStudio.test.js plus previous hotfix tests.
- [ ] Visual inspection: browser blocked both local development addresses (ERR_BLOCKED_BY_CLIENT); interaction tests passed in jsdom. No visual verification claimed.
- [x] Python selected regressions; compile changed modules.
- [x] Write Russian update/usage note, package full ZIP excluding node_modules, runtime caches and user projects.
- [x] Verify ZIP, persist deliverable and return sandbox link.
