from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable

from ..core.utils import OperationCancelled


class ResourceManager:
    """Process-wide, thread-reentrant leases for heavy shared resources.

    A high-level pipeline stage may legitimately call a lower-level AI helper
    while it already owns the same GPU lease. Counting that nested acquisition
    as a second consumer deadlocks forever when capacity is one. Ownership depth
    is therefore tracked per thread while ``active`` continues to represent
    physical consumers for cross-thread admission control.
    """

    _condition = threading.Condition(threading.Lock())
    _states: dict[str, dict[str, Any]] = {}

    @classmethod
    @contextmanager
    def lease(cls, resource: str, *, capacity: int = 1, cancel_check: Callable[[], bool] | None = None, poll_seconds: float = 0.2):
        name = str(resource)
        requested_limit = max(1, int(capacity))
        owner = threading.get_ident()
        acquired = False
        with cls._condition:
            state = cls._states.setdefault(name, {"active": 0, "limit": requested_limit, "owners": {}})
            state["limit"] = min(int(state.get("limit", requested_limit)), requested_limit)
            owners = state.setdefault("owners", {})
            if cancel_check and cancel_check():
                raise OperationCancelled(f"Cancelled while waiting for {resource} resource")
            if int(owners.get(owner, 0)) > 0:
                owners[owner] = int(owners[owner]) + 1
                acquired = True
            else:
                while int(state.get("active", 0)) >= int(state.get("limit", 1)):
                    if cancel_check and cancel_check():
                        raise OperationCancelled(f"Cancelled while waiting for {resource} resource")
                    cls._condition.wait(timeout=max(0.05, float(poll_seconds)))
                if cancel_check and cancel_check():
                    raise OperationCancelled(f"Cancelled while waiting for {resource} resource")
                state["active"] = int(state.get("active", 0)) + 1
                owners[owner] = 1
                acquired = True
        try:
            yield
        finally:
            if acquired:
                with cls._condition:
                    state = cls._states.get(name)
                    if state is not None:
                        owners = state.setdefault("owners", {})
                        remaining = max(0, int(owners.get(owner, 0)) - 1)
                        if remaining:
                            owners[owner] = remaining
                        else:
                            owners.pop(owner, None)
                            state["active"] = max(0, int(state.get("active", 0)) - 1)
                    cls._condition.notify_all()

    @classmethod
    def snapshot(cls) -> dict[str, dict[str, int]]:
        with cls._condition:
            return {
                name: {
                    "active": int(state.get("active", 0)),
                    "limit": int(state.get("limit", 1)),
                    "owner_count": len(state.get("owners", {})),
                    "lease_depth": sum(int(depth) for depth in state.get("owners", {}).values()),
                }
                for name, state in cls._states.items()
            }
