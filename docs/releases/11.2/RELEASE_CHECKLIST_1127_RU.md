# Release checklist 11.2.7

- [x] Canonical identity и package/lock/frontend/backend agreement.
- [x] Clean frontend build и source/dist SHA-256 manifest.
- [x] Повторная сборка: одинаковые inputs/outputs.
- [x] Frontend DOM/unit/contracts: 112/112; Electron policy: 10/10.
- [x] Real FFmpeg/Shorts и Unicode path checks.
- [x] Ruff, compile, syntax; dependency scanners после обновления.
- [ ] Python suite полностью зелёный: 568 passed, 1 native MediaPipe failure в этой среде.
- [ ] MediaPipe native и multi-face visual acceptance.
- [ ] Windows/NVIDIA/NTFS/Defender/installer/portable/signing/update/uninstall.
- [ ] Реальные 30+ VOD и quality/performance benchmark evidence.
- [ ] Real Ollama/Whisper/OOM/cancel/retry/TTFT.
- [ ] Real PostgreSQL/Alembic recovery, SMTP, Docker deployment.
- [ ] Приватная YouTube test upload с подтверждением владельца.
- [ ] Полный accessibility/contrast/HiDPI/keyboard walkthrough.
- [ ] Production URLs/license key и действительные release evidence.
- [ ] `python tools/release/check_mass_release.py --strict` возвращает 0.

Последовательность Windows acceptance и причины каждого NOT VERIFIED приведены в TECHNICAL_AUDIT_11.2.7_RU.md. Архивный SHA-256/contract проверяется отдельно после финальной упаковки.
