from __future__ import annotations

from typing import Any, Protocol

from src.domain.evidence.entities import EvidenceCard


class PairContextRunner(Protocol):
    def __call__(self, drug_a: str, drug_b: str, **kwargs: Any) -> dict[str, Any]:
        ...


class AnswerGenerator(Protocol):
    def __call__(self, context: dict[str, Any], mode: str, **kwargs: Any) -> dict[str, Any]:
        ...


class FollowUpGenerator(Protocol):
    def __call__(
        self,
        context: dict[str, Any],
        mode: str,
        *,
        question: str,
        history: list[dict[str, Any]],
        prior_assessment: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        ...


class AnalysisSnapshotStore(Protocol):
    def get_analysis_snapshot(self, analysis_id: str) -> dict[str, Any] | None:
        ...


class EvidenceCardStore(Protocol):
    def append_evidence_card(self, card: EvidenceCard) -> None:
        ...
