from __future__ import annotations

import threading
from pathlib import Path
from typing import Hashable

_registry_guard = threading.Lock()
_metadata_locks: dict[str, threading.RLock] = {}
_lifecycle_locks: dict[str, threading.RLock] = {}


def _key(value: str | Path | Hashable) -> str:
    if isinstance(value, Path):
        try:
            return str(value.expanduser().resolve())
        except Exception:
            return str(value)
    return str(value)


def _get(registry: dict[str, threading.RLock], value: str | Path | Hashable) -> threading.RLock:
    key = _key(value)
    with _registry_guard:
        lock = registry.get(key)
        if lock is None:
            lock = threading.RLock()
            registry[key] = lock
        return lock


def project_metadata_lock(value: str | Path | Hashable) -> threading.RLock:
    """Serialize read-modify-write mutations of one project's metadata.

    Atomic file replacement protects against torn JSON, but without this lock two
    writers can both read the same old project.json and silently erase each
    other's unrelated fields.  All metadata RMW paths should share this lock.
    """

    return _get(_metadata_locks, value)


def project_lifecycle_lock(value: str | Path | Hashable) -> threading.RLock:
    """Serialize destructive lifecycle operations with background-job start."""

    return _get(_lifecycle_locks, value)
