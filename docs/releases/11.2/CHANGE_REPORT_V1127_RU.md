# Изменения Highlight Studio 11.2.7

Исправленный кандидат; mass-release gate BLOCKED. Полный отчёт и границы: [TECHNICAL_AUDIT_11.2.7_RU.md](TECHNICAL_AUDIT_11.2.7_RU.md).

## Функциональные изменения

- AI failure/отмена/пустой монтаж не стирают сохранённые metadata.
- JSON backup валидируется и заменяется атомарно.
- FFprobe reject для нечисловой/неположительной длительности и отсутствующего видеопотока.
- Metadata job проверяет результат и честно называет локальный fallback.
- Credentials скрываются до записи лога, в API журналов и AI error; camelCase redaction.
- Последовательный опрос jobs, cleanup/abort и защита от устаревшего ответа.

## Release / инженерные изменения

- Canonical release identity, fail-fast при повреждении identity, версии package/lock/docs проверяются совместно.
- Build manifest SHA-256 источников и результатов; public/dist parity, stale assets detection.
- Release CI собирает frontend перед упаковкой; archive staging, case collision/traversal rejection.
- Обновлены cryptography и transitive npm locks; scanners теперь не находят известных уязвимостей.
- Исправлены устаревшие test harnesses без удаления продуктовых гарантий; добавлен production DOM test.
- История перенесена в docs/releases/11.2/history; новый пакет документов, gate и воспроизводимые logs.
- Точечная lint-уборка без глобального architecture rewrite. Electron остаётся production shell, Tauri — experimental.

## Проверки

Итог: Python 568 passed / 1 failed (MediaPipe: отсутствует libGLESv2.so.2), frontend 112/112, Electron 10/10. Ruff/compile/build/identity/checksums проходят; ESLint: 1 warning. Повторная сборка побайтово воспроизводит manifest/outputs. Windows/GPU/live services/installer NOT VERIFIED. Native test не выключен. Существующий mass-release gate: 21 blockers.

Список каждого файла: [CHANGED_FILES_1127.md](CHANGED_FILES_1127.md).
