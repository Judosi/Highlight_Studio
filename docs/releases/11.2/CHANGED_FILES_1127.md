# Файлы, изменённые в аудите 11.2.7

Сравнение с исходным ZIP. RELEASE_CHECKSUMS.json генерируется заново при упаковке; runtime/cache/dependencies исключены.

## Изменены

- `.github/workflows/ci.yml`
- `README.md`
- `backend/requirements.txt`
- `backend/src/highlight_studio/api/app.py`
- `backend/src/highlight_studio/core/artifacts.py`
- `backend/src/highlight_studio/core/revisions.py`
- `backend/src/highlight_studio/core/settings.py`
- `backend/src/highlight_studio/core/utils.py`
- `backend/src/highlight_studio/infrastructure/auth/router.py`
- `backend/src/highlight_studio/infrastructure/support_bundle.py`
- `backend/src/highlight_studio/integrations/youtube/publisher.py`
- `backend/src/highlight_studio/services/gates.py`
- `backend/src/highlight_studio/services/hardware.py`
- `backend/src/highlight_studio/services/pipeline.py`
- `deploy/web.Dockerfile`
- `desktop/README.md`
- `desktop/electron/package-lock.json`
- `desktop/tauri/README.md`
- `docs/ARCHITECTURE.md`
- `frontend/dist/index.html`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/src/app/App.jsx`
- `frontend/tests/app.integration.test.js`
- `frontend/tests/audit1126.test.js`
- `frontend/tests/compactUi.contract.test.js`
- `frontend/tests/designStages.contract.test.js`
- `frontend/tests/manualReviewNavigation10158.contract.test.js`
- `frontend/tests/navigationStability.contract.test.js`
- `frontend/tests/paidBeta.contract.test.js`
- `frontend/tests/shortsEditor1110.contract.test.js`
- `frontend/tests/workspaceV155.contract.test.js`
- `frontend/vite.config.js`
- `tests/test_ai_runtime_101512.py`
- `tests/test_analysis_montage_hotfix.py`
- `tests/test_audit_regressions_1126.py`
- `tests/test_duration_and_review_feedback_1122.py`
- `tests/test_folder_architecture_101519.py`
- `tests/test_functional_remediation_10154.py`
- `tests/test_media_integration.py`
- `tests/test_phase1_101513.py`
- `tests/test_phase3_101515.py`
- `tests/test_release_tools.py`
- `tests/test_shorts_prepare_hotfix.py`
- `tests/test_shorts_reframing.py`
- `tests/test_twitch_turbo_restore.py`
- `tests/test_windows_file_lock_hotfix.py`
- `tests/test_workflow_navigation_101519.py`
- `tools/diagnostics/audit_frontend_layout.py`
- `tools/diagnostics/gpu_runtime_check.py`
- `tools/diagnostics/test_manual_step_101513.py`
- `tools/diagnostics/test_production_stabilization_101515.py`
- `tools/diagnostics/test_review_add_101513.py`
- `tools/diagnostics/test_terminal_sync_101513.py`
- `tools/diagnostics/verify_release_identity.py`
- `tools/release/make_release.py`
- `tools/release/release_layout.py`
- `tools/release/verify_archive.py`

## Добавлены

- `docs/releases/11.2/evidence-1127/release-final.txt`
- `docs/releases/11.2/evidence-1127/archive-final.txt`

- `.gitattributes`
- `docs/audits/AUDIT_11.2.7.md`
- `docs/releases/11.2/BUILD_INFO_V1127_RU.txt`
- `docs/releases/11.2/CHANGE_REPORT_V1127_RU.md`
- `docs/releases/11.2/RELEASE_CHECKLIST_1127_RU.md`
- `docs/releases/11.2/TECHNICAL_AUDIT_11.2.7_RU.md`
- `docs/releases/11.2/evidence-1127/audit-electron-final.txt`
- `docs/releases/11.2/evidence-1127/audit-frontend-fix.txt`
- `docs/releases/11.2/evidence-1127/audit-python-final.txt`
- `docs/releases/11.2/evidence-1127/build-repro.txt`
- `docs/releases/11.2/evidence-1127/compile-final.txt`
- `docs/releases/11.2/evidence-1127/electron-final.txt`
- `docs/releases/11.2/evidence-1127/eslint-final.txt`
- `docs/releases/11.2/evidence-1127/frontend-final.txt`
- `docs/releases/11.2/evidence-1127/identity-final.txt`
- `docs/releases/11.2/evidence-1127/mass-release.json`
- `docs/releases/11.2/evidence-1127/media-regression.txt`
- `docs/releases/11.2/evidence-1127/new-regressions-before.txt`
- `docs/releases/11.2/evidence-1127/python-final.txt`
- `docs/releases/11.2/evidence-1127/routes.txt`
- `docs/releases/11.2/evidence-1127/ruff-final.txt`
- `docs/releases/11.2/history/HOTFIX_35MIN_RU.txt`
- `docs/releases/11.2/history/SHORTS_FIX_RU.txt`
- `docs/releases/11.2/history/UPDATE_ANALYSIS_MONTAGE_RU.txt`
- `docs/releases/11.2/history/UPDATE_SHORTS_REFRAME_RU.txt`
- `docs/releases/11.2/history/UPDATE_SHORTS_STUDIO_RU.txt`
- `docs/releases/11.2/history/UPDATE_TASK_CENTER_RU.txt`
- `frontend/dist/assets/index-1127-DJr-njFk.js`
- `frontend/dist/build-manifest.json`
- `frontend/scripts/build-manifest.mjs`
- `frontend/src/lib/serialPoll.js`
- `frontend/task-center-fixture.jsx`
- `frontend/tests/production.integration.test.js`
- `frontend/tests/productionBuild.js`
- `frontend/tests/serialPoll.test.js`
- `tests/test_audit_regressions_1127.py`
- `tests/test_release_integrity_1127.py`
- `tools/release/checks/CHECK_V1127.bat`
- `tools/release/frontend_integrity.py`
- `docs/releases/11.2/CHANGED_FILES_1127.md`

## Удалены из прежнего расположения

- `HOTFIX_35MIN_RU.txt`
- `SHORTS_FIX_RU.txt`
- `UPDATE_ANALYSIS_MONTAGE_RU.txt`
- `UPDATE_SHORTS_REFRAME_RU.txt`
- `UPDATE_SHORTS_STUDIO_RU.txt`
- `UPDATE_TASK_CENTER_RU.txt`
- `frontend/dist/assets/index-1127-BVhJ4dbm.js`

Шесть root TXT сохранены в docs/releases/11.2/history. Старый hashed JS заменён новой Vite-сборкой; все остальные изменения перечислены выше.
