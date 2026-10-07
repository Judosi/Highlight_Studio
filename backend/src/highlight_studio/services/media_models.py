"""Domain data carried through the media-analysis pipeline.

These dataclasses intentionally have no I/O or service dependencies.  Keeping
them separate from the 11k-line pipeline makes their contract reusable without
importing FFmpeg/AI orchestration. ``pipeline`` re-exports both names for legacy
callers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Candidate:
    id: int
    start: float
    end: float
    score: float
    title: str
    reason: str
    text_preview: str = ""
    visual_score: float = 0.0
    audio_score: float = 0.0
    penalty_score: float = 0.0
    transcript_score: float = 0.0
    ai_score: float = 0.0
    confidence: float = 0.0
    confidence_reason: str = ""
    decision: str = "maybe"
    hook_potential: str = "medium"
    context_before_seconds: float = 0.0
    context_after_seconds: float = 0.0
    moment_type: str = "general"
    standalone_clarity: float = 0.5
    ocr_score: float = 0.0
    irl_score: float = 0.0
    story_role: str = "body"
    ai_explanation: str = ""
    what_happens: str = ""
    why_selected: str = ""
    viewer_value: str = ""
    risk: str = ""
    # v10.15.14 semantic quality layer.  These fields deliberately travel with
    # the candidate through micro-cut/dedup/storyline so a visually exciting
    # replay/reconnect screen cannot be reclassified as primary live content
    # merely because a later stage sees laughter, chat or a donation.
    content_class: str = "unknown"
    content_class_confidence: float = 0.0
    content_evidence: str = ""
    semantic_topic: str = ""
    parent_id: int = 0
    template_signature: str = ""
    candidate_id: str = ""
    display_order: int = 0

    def __post_init__(self):
        if not self.candidate_id:
            raw = f"{self.id}|{self.start:.3f}|{self.end:.3f}|{self.title}"
            self.candidate_id = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:20]
