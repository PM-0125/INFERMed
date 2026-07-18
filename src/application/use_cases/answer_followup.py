from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any, Callable

from src.application.ports import AnalysisSnapshotStore, FollowUpGenerator, PairContextRunner
from src.application.use_cases.analyze_medication_set import (
    PairContextResult,
    build_medication_set_context_from_pair_results,
    decision_from_context,
    evidence_cards_from_context,
)
from src.config.settings import Settings, get_settings
from src.domain.explanation.entities import FollowUpSession


@dataclass(frozen=True)
class AnswerFollowUpCommand:
    question: str
    drugs: list[str]
    mode: str = "doctor"
    context_id: str | None = None
    patient_context: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    prior_assessment: str | None = None
    follow_up_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FollowUpAnswer:
    answer: str
    cited_cards: list[str]
    session: FollowUpSession
    context_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "cited_cards": self.cited_cards,
            "citedCards": self.cited_cards,
            "session": self.session.to_dict(),
            "context_source": self.context_source,
        }


class AnswerFollowUpUseCase:
    def __init__(
        self,
        *,
        followup_generator: FollowUpGenerator,
        context_runner: PairContextRunner,
        cited_cards_from_context: Callable[[dict[str, Any]], list[str]],
        snapshot_store: AnalysisSnapshotStore | None = None,
        settings: Settings | None = None,
    ):
        self.followup_generator = followup_generator
        self.context_runner = context_runner
        self.cited_cards_from_context = cited_cards_from_context
        self.snapshot_store = snapshot_store
        self.settings = settings or get_settings()

    def execute(self, command: AnswerFollowUpCommand) -> FollowUpAnswer:
        if command.follow_up_count >= 3:
            raise ValueError("Follow-up limit reached for this interaction. Start a new analysis to continue.")
        context, context_source = self._context_for_followup(command)
        if command.patient_context:
            context = _context_with_patient_context(context, command.patient_context)
        answer = self.followup_generator(
            context,
            command.mode,
            question=command.question,
            history=command.history,
            prior_assessment=command.prior_assessment,
            model_name=None,
        )
        session = FollowUpSession(
            analysis_id=command.context_id or "ad_hoc_followup",
            audience=command.mode,
            turns=command.history,
            follow_up_count=command.follow_up_count + 1,
        )
        return FollowUpAnswer(
            answer=_compact_scoped_followup(str(answer.get("text") or "").strip() or "No answer was generated."),
            cited_cards=self.cited_cards_from_context(context),
            session=session,
            context_source=context_source,
        )

    def _context_for_followup(self, command: AnswerFollowUpCommand) -> tuple[dict[str, Any], str]:
        snapshot = self._snapshot_for_context_id(command.context_id)
        if snapshot and isinstance(snapshot.get("context"), dict):
            return snapshot["context"], "analysis_snapshot"

        drugs = command.drugs
        if len(drugs) == 2:
            return self.context_runner(drugs[0], drugs[1]), "pair_context_cache"

        pair_results: list[PairContextResult] = []
        analysis_id = command.context_id or "followup_rebuilt_context"
        for pair in combinations(drugs, 2):
            context = self.context_runner(pair[0], pair[1])
            cards = evidence_cards_from_context(str(analysis_id), context, list(pair))
            decision = decision_from_context(str(analysis_id), context, cards, list(pair))
            pair_results.append(PairContextResult(pair=(pair[0], pair[1]), context=context, evidence_cards=cards, decision=decision))
        return (
            build_medication_set_context_from_pair_results(
                analysis_id=str(analysis_id),
                medications=drugs,
                pair_results=pair_results,
                data_mode=self.settings.data_mode,
            ),
            "rebuilt_medication_set_context",
        )

    def _snapshot_for_context_id(self, context_id: str | None) -> dict[str, Any] | None:
        if not context_id or self.snapshot_store is None:
            return None
        return self.snapshot_store.get_analysis_snapshot(context_id)


def _context_with_patient_context(context: dict[str, Any], patient_context: dict[str, Any]) -> dict[str, Any]:
    out = dict(context)
    medset = dict(out.get("medication_set") or {})
    medset["patient_context"] = patient_context
    out["medication_set"] = medset
    return out


def _compact_scoped_followup(answer: str) -> str:
    prohibited = ("## Bottom Line", "## Interaction Mechanism", "## Clinical Concern", "## Monitoring & Actions", "## Evidence Limitations")
    if not any(section in answer for section in prohibited):
        return answer
    keep: list[str] = []
    current: list[str] = []
    current_heading = ""
    for line in answer.splitlines():
        if line.startswith("## "):
            if current and current_heading not in prohibited:
                keep.extend(current)
            current = [line]
            current_heading = line.strip()
        else:
            current.append(line)
    if current and current_heading not in prohibited:
        keep.extend(current)
    compact = "\n".join(line for line in keep if line.strip()).strip()
    return compact or (
        "## Direct Answer\n"
        "The follow-up response was constrained because it repeated the initial assessment. "
        "Please ask a narrower patient-specific question.\n\n"
        "## What To Monitor\n"
        "Review the evidence panel and clinical context qualitatively.\n\n"
        "## Evidence Boundary\n"
        "No new evidence was added for this follow-up."
    )
