from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import combinations
from copy import deepcopy
from typing import Any, Callable
import uuid

from src.application.ports import (
    AnalysisSnapshotStore,
    FollowUpGenerator,
    PairContextRunner,
)
from src.application.use_cases.analyze_medication_set import (
    PairContextResult,
    build_medication_set_context_from_pair_results,
    decision_from_context,
    evidence_cards_from_context,
)
from src.config.settings import Settings, get_settings
from src.domain.explanation.entities import FollowUpSession
from src.domain.patient.services import merge_patient_context
from src.domain.evidence.entities import EvidenceCard
from src.domain.explanation.action_support import enforce_action_support
from src.domain.explanation.presentation import render_citations


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
    publication: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "cited_cards": self.cited_cards,
            "citedCards": self.cited_cards,
            "session": self.session.to_dict(),
            "context_source": self.context_source,
            "publication": self.publication,
        }


class AnswerFollowUpUseCase:
    def __init__(
        self,
        *,
        followup_generator: FollowUpGenerator,
        context_runner: PairContextRunner,
        cited_cards_from_context: Callable[[dict[str, Any]], list[str]],
        snapshot_store: AnalysisSnapshotStore | None = None,
        explanation_recorder: Callable[..., None] | None = None,
        settings: Settings | None = None,
    ):
        self.followup_generator = followup_generator
        self.context_runner = context_runner
        self.cited_cards_from_context = cited_cards_from_context
        self.snapshot_store = snapshot_store
        self.explanation_recorder = explanation_recorder
        self.settings = settings or get_settings()

    def execute(self, command: AnswerFollowUpCommand) -> FollowUpAnswer:
        if command.follow_up_count >= 3:
            raise ValueError(
                "Follow-up limit reached for this interaction. Start a new analysis to continue."
            )
        context, context_source = self._context_for_followup(command)
        if command.patient_context:
            context = _context_with_patient_context(context, command.patient_context)
        # Rebuilt/legacy contexts receive the same source contract as analyses.
        if command.mode.lower() in {"doctor", "clinician"} and not context.get(
            "reasoning_input"
        ):
            cards = evidence_cards_from_context(
                command.context_id or "followup", context, command.drugs
            )
            context["reasoning_input"] = {
                "schema_version": 1,
                "evidence": [c.to_dict() for c in cards],
                "decision": {"drugs": command.drugs},
                "patient_context": (context.get("medication_set") or {}).get(
                    "patient_context"
                )
                or {},
            }
        answer = self.followup_generator(
            context,
            command.mode,
            question=command.question,
            history=command.history,
            prior_assessment=command.prior_assessment,
            model_name=None,
        )
        text = str(answer.get("text") or "").strip()
        if (
            not text
            or (answer.get("meta") or {}).get("error")
            or text.startswith(
                ("Unable to generate", "No answer was generated", "No generated answer")
            )
        ):
            raise RuntimeError(
                "Follow-up generation failed; no clinical answer was produced."
            )
        raw_cards = (context.get("reasoning_input") or {}).get("evidence") or []
        cards = [EvidenceCard(**item) for item in raw_cards]
        if not cards and len(command.drugs) == 2:
            cards = evidence_cards_from_context(
                command.context_id or "followup", context, command.drugs
            )
        publication = enforce_action_support(
            text if answer.get("doctor_contract") else _compact_scoped_followup(text),
            cards,
            command.drugs,
            review_status=(answer.get("review") or {}).get("status"),
            patient_context=(context.get("medication_set") or {}).get(
                "patient_context"
            ),
        )
        if self.explanation_recorder is not None:
            self.explanation_recorder(
                explanation_id="followup_" + uuid.uuid4().hex,
                analysis_id=command.context_id or "ad_hoc_followup",
                answer_text=text,
                verification={
                    "publication": publication.summary(),
                    "findings": publication.findings,
                    "doctor_contract": answer.get("doctor_contract"),
                },
                model_name=(answer.get("meta") or {}).get("model"),
            )
        session = FollowUpSession(
            analysis_id=command.context_id or "ad_hoc_followup",
            audience=command.mode,
            turns=command.history,
            follow_up_count=command.follow_up_count + 1,
        )
        return FollowUpAnswer(
            answer=render_citations(publication.text, cards, command.mode),
            cited_cards=self.cited_cards_from_context(context),
            session=session,
            context_source=context_source,
            publication={
                **publication.summary(),
                "doctor_contract": answer.get("doctor_contract"),
            },
        )

    def _context_for_followup(
        self, command: AnswerFollowUpCommand
    ) -> tuple[dict[str, Any], str]:
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
            decision = decision_from_context(
                str(analysis_id), context, cards, list(pair)
            )
            pair_results.append(
                PairContextResult(
                    pair=(pair[0], pair[1]),
                    context=context,
                    evidence_cards=cards,
                    decision=decision,
                )
            )
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


def _context_with_patient_context(
    context: dict[str, Any], patient_context: dict[str, Any]
) -> dict[str, Any]:
    out = deepcopy(context)
    medset = dict(out.get("medication_set") or {})
    patient = merge_patient_context(medset.get("patient_context"), patient_context)
    medset["patient_context"] = patient.to_dict()
    out["medication_set"] = medset
    if out.get("reasoning_input"):
        from src.domain.decision.rules import (
            patient_amplifiers,
            missing_patient_factors_for_decision,
        )

        reasoning = out["reasoning_input"]
        decision = reasoning.get("decision") or {}
        decision["patient_amplifiers"] = patient_amplifiers(patient)
        decision["missing_patient_factors"] = missing_patient_factors_for_decision(
            patient
        )
        reasoning["decision"] = decision
        reasoning["patient_context"] = patient.to_dict()
        reasoning["patient_update_note"] = (
            "Patient modifiers recomputed; interaction risk remains evidence-based, not a validated patient-specific risk prediction."
        )
    return out


def _compact_scoped_followup(answer: str) -> str:
    prohibited = (
        "## Bottom Line",
        "## Interaction Mechanism",
        "## Clinical Concern",
        "## Monitoring & Actions",
        "## Evidence Limitations",
    )
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
