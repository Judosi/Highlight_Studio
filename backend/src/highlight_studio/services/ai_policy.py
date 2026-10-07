"""Pure AI batching and validation policy for the analysis pipeline.

No filesystem, HTTP, FFmpeg or model-client I/O belongs here.  Keeping these
rules isolated makes the retry/completeness behavior unit-testable without
loading the whole media pipeline.
"""

from __future__ import annotations

import math
from typing import Any, Callable

def strict_ai_enabled(settings: dict[str, Any]) -> bool:
    """When enabled, the app must not silently replace AI work with heuristics/fallbacks."""
    return bool(settings.get("ai_strict_mode", False) or settings.get("full_ai_coverage", False))


def ai_retry_count(settings: dict[str, Any], default: int = 3) -> int:
    try:
        return max(1, min(10, int(settings.get("ai_retry_count", default) or default)))
    except Exception:
        return default


def ai_batch_completeness_required(settings: dict[str, Any]) -> bool:
    """Quality-first default: a completed AI stage may not silently lose IDs."""
    return bool(settings.get("ai_batch_completeness_required", True) or strict_ai_enabled(settings))


def ai_prompt_char_budget(settings: dict[str, Any]) -> int:
    """Conservative prompt budget derived from Ollama context size.

    Russian transcript text often tokenizes denser than English. 10.15.12 sent
    ~11-13k character prompts into a 4096-token Qwen context and the model began
    returning ``{"error": ...}`` / wrong JSON shapes. We keep all transcript
    material, but group fewer blocks per request.
    """
    try:
        ctx = max(2048, min(16384, int(settings.get("ollama_num_ctx", 4096) or 4096)))
    except Exception:
        ctx = 4096
    explicit = settings.get("ai_prompt_char_budget")
    if explicit not in (None, "", 0):
        try:
            return max(5000, min(30000, int(explicit)))
        except Exception:
            pass
    return max(7000, min(22000, int(ctx * 2.0)))


def plan_prompt_safe_batches(
    items: list[Any],
    max_items: int,
    prompt_builder: Callable[[list[Any]], str],
    max_prompt_chars: int,
) -> list[list[Any]]:
    """Pack items without truncating them and without overflowing local LLM context."""
    max_items = max(1, int(max_items or 1))
    max_prompt_chars = max(1000, int(max_prompt_chars or 1000))
    planned: list[list[Any]] = []
    current: list[Any] = []
    for item in items:
        proposed = current + [item]
        too_many = len(proposed) > max_items
        too_large = bool(current) and len(prompt_builder(proposed)) > max_prompt_chars
        if too_many or too_large:
            planned.append(current)
            current = [item]
        else:
            current = proposed
    if current:
        planned.append(current)
    return planned


def require_complete_ai_result(stage: str, expected_ids: set[int], actual_ids: set[int], settings: dict[str, Any]):
    missing = sorted(expected_ids - actual_ids)
    if missing and ai_batch_completeness_required(settings):
        raise RuntimeError(
            f"{stage} вернул не все ID. Не обработаны: {missing[:20]}{'...' if len(missing) > 20 else ''}. "
            "Highlight Studio остановил этап, чтобы не выдавать неполный AI-анализ за успешный."
        )


def extract_ai_items(data: Any, key: str) -> list[dict[str, Any]]:
    """Accept common local-LLM JSON shapes and return a list of item dicts."""
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        if isinstance(data.get(key), list):
            raw = data.get(key) or []
        elif isinstance(data.get("items"), list):
            raw = data.get("items") or []
        elif isinstance(data.get("results"), list):
            raw = data.get("results") or []
        elif isinstance(data.get("segments"), list):
            raw = data.get("segments") or []
        elif data.get("id") is not None:
            raw = [data]
        else:
            raw = []
    else:
        raw = []
    return [x for x in raw if isinstance(x, dict)]


def normalize_ai_items_by_id(items: list[dict[str, Any]], expected_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Convert model items to {id:item}, dropping invalid/duplicate IDs safely."""
    by_id: dict[int, dict[str, Any]] = {}
    for item in items:
        try:
            raw_id = item.get("id")
            if isinstance(raw_id, str):
                raw_id = raw_id.strip().replace("ID=", "").replace("id=", "")
            item_id = int(raw_id)
        except Exception:
            continue
        if item_id in expected_ids and item_id not in by_id:
            by_id[item_id] = item
    return by_id


def scored_ai_items_complete(by_id: dict[int, dict[str, Any]], expected_ids: set[int]) -> bool:
    if set(by_id) != set(expected_ids):
        return False
    for item_id in expected_ids:
        item = by_id.get(item_id) or {}
        try:
            score = float(item.get("score"))
        except Exception:
            return False
        if math.isnan(score) or math.isinf(score):
            return False
    return True


def strict_missing_message(stage: str, expected_ids: set[int], actual_ids: set[int]) -> str:
    missing = sorted(expected_ids - actual_ids)
    return f"{stage}: не обработаны ID {missing[:20]}{'...' if len(missing) > 20 else ''}"
