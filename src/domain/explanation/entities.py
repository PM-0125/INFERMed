from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExplanationRequest:
    analysis_id: str
    audience: str
    decision: dict[str, Any]
    evidence_card_ids: list[str]
    question: str | None = None
    patient_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExplanationDraft:
    explanation_id: str
    analysis_id: str
    answer_text: str
    model_name: str | None = None
    prompt_hash: str | None = None
    evidence_card_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerifiedExplanation:
    explanation_id: str
    analysis_id: str
    answer_text: str
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FollowUpSession:
    analysis_id: str
    audience: str
    turns: list[dict[str, str]] = field(default_factory=list)
    follow_up_count: int = 0
    max_follow_ups: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def remaining(self) -> int:
        return max(self.max_follow_ups - self.follow_up_count, 0)
