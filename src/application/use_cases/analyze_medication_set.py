from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from src.application.commands import AnalyzeMedicationSetCommand
from src.application.event_bus import EventBus
from src.application.events import DomainEvent, stable_hash
from src.application.tool_planner import EvidencePlan, ToolPlanner
from src.config.settings import Settings, get_settings
from src.domain.decision.entities import InteractionDecision
from src.domain.evidence.entities import EvidenceCard, evidence_id_for
from src.domain.mechanism.entities import MechanismCluster, MechanismEdge, MechanismGraph, MechanismNode
from src.domain.medication.entities import MedicationConcept
from src.domain.medication.services import medication_set_key, normalize_medication
from src.infrastructure.persistence.event_store import SQLiteEventStore

RagRunner = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class MedicationSetAnalysis:
    analysis_id: str
    request_id: str | None
    medications: list[MedicationConcept]
    evidence_plan: EvidencePlan
    executed_pair: tuple[str, str]
    rag_output: dict[str, Any]
    evidence_cards: list[EvidenceCard]
    mechanism_graph: MechanismGraph
    decision: InteractionDecision
    events: list[DomainEvent]

    def to_read_model(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "analysisId": self.analysis_id,
            "request_id": self.request_id,
            "normalized_medications": [item.to_dict() for item in self.medications],
            "normalizedMedications": [item.to_dict() for item in self.medications],
            "executed_pair": list(self.executed_pair),
            "evidence_plan": self.evidence_plan.to_dict(),
            "evidencePlan": self.evidence_plan.to_dict(),
            "decision": self.decision.to_dict(),
            "mechanism_graph": self.mechanism_graph.to_dict(),
            "mechanismGraph": self.mechanism_graph.to_dict(),
            "domain_events": [event.to_dict() for event in self.events],
        }


class AnalyzeMedicationSetUseCase:
    """Compatibility wrapper that gives the current pair RAG an auditable lifecycle."""

    def __init__(
        self,
        *,
        rag_runner: RagRunner,
        settings: Settings | None = None,
        event_store: SQLiteEventStore | None = None,
        tool_planner: ToolPlanner | None = None,
    ):
        self.rag_runner = rag_runner
        self.settings = settings or get_settings()
        self.event_store = event_store if event_store is not None else _default_event_store(self.settings)
        self.event_bus = EventBus(self.event_store)
        self.tool_planner = tool_planner or ToolPlanner()

    def execute(self, command: AnalyzeMedicationSetCommand) -> MedicationSetAnalysis:
        analysis_id = "ana_" + uuid.uuid4().hex
        request_id = command.request_id or "req_" + uuid.uuid4().hex

        concepts = [normalize_medication(item) for item in command.medications]
        concepts = [item for item in concepts if item.normalized_name]
        if len(concepts) < 2:
            raise ValueError("At least two non-empty medications are required")

        medset_key = medication_set_key(concepts)
        if self.event_store is not None:
            self.event_store.create_analysis_run(
                analysis_id=analysis_id,
                request_hash=command.request_hash(),
                normalized_drug_set_key=medset_key,
                data_mode=self.settings.data_mode,
            )

        self._publish(
            analysis_id,
            "MedicationSetAnalysisRequested",
            request_id=request_id,
            payload={
                "medication_count": len(concepts),
                "audience": command.audience,
                "analysis_depth": command.analysis_depth,
                "refresh_evidence": command.refresh_evidence,
            },
            status="requested",
        )

        for concept in concepts:
            self._publish(
                analysis_id,
                "MedicationNormalized",
                request_id=request_id,
                payload=concept.to_dict(),
                component="medication_identity",
            )

        evidence_plan = self.tool_planner.create_plan(concepts, data_mode=self.settings.data_mode)
        self._publish(
            analysis_id,
            "EvidencePlanCreated",
            request_id=request_id,
            payload=evidence_plan.to_dict(),
            component="tool_planner",
        )

        # Pair compatibility path: execute the first prioritized pair now.
        # Later N-drug work can execute prioritized pairs and cluster tools here.
        executed_pair = evidence_plan.priority_pairs[0]
        self._publish(
            analysis_id,
            "SourceFetchRequested",
            request_id=request_id,
            payload={"pair": list(executed_pair), "tool": "current_pair_rag_pipeline"},
            component="evidence",
            source_tool_name="current_pair_rag_pipeline",
            status="requested",
        )

        try:
            rag_output = self.rag_runner(
                executed_pair[0],
                executed_pair[1],
                mode=command.audience,
                use_cache_context=not command.refresh_evidence,
                use_cache_response=False,
                model_name=None,
            )
        except Exception as exc:
            self._publish(
                analysis_id,
                "SourceFetchFailed",
                request_id=request_id,
                payload={"pair": list(executed_pair)},
                component="evidence",
                source_tool_name="current_pair_rag_pipeline",
                status="failed",
                error_summary=str(exc),
            )
            if self.event_store is not None:
                self.event_store.update_analysis_status(analysis_id, "failed")
            raise

        context = rag_output.get("context") or {}
        answer = rag_output.get("answer") or {}
        answer_text = str(answer.get("text") or "").strip()

        self._publish(
            analysis_id,
            "SourceFetchSucceeded",
            request_id=request_id,
            payload={
                "pair": list(executed_pair),
                "context_hash": stable_hash(context),
                "source_count": len(context.get("source_status") or []),
            },
            component="evidence",
            source_tool_name="current_pair_rag_pipeline",
        )

        evidence_cards = _evidence_cards_from_context(analysis_id, context, list(executed_pair))
        for card in evidence_cards:
            if self.event_store is not None:
                self.event_store.append_evidence_card(card)
        self._publish(
            analysis_id,
            "EvidenceNormalized",
            request_id=request_id,
            payload={"evidence_card_count": len(evidence_cards)},
            component="evidence",
        )

        mechanism_graph = _mechanism_graph_from_context(context, evidence_cards, list(executed_pair))
        self._publish(
            analysis_id,
            "MechanismGraphBuilt",
            request_id=request_id,
            payload={
                "node_count": len(mechanism_graph.nodes),
                "edge_count": len(mechanism_graph.edges),
                "cluster_count": len(mechanism_graph.clusters),
            },
            component="mechanism",
        )

        decision = _decision_from_context(analysis_id, context, evidence_cards, list(executed_pair))
        self._publish(
            analysis_id,
            "ClinicalDecisionCreated",
            request_id=request_id,
            payload=decision.to_dict(),
            component="decision",
        )

        explanation_id = "exp_" + stable_hash({"analysis_id": analysis_id, "answer": answer_text})[:16]
        if self.event_store is not None:
            self.event_store.append_explanation_record(
                explanation_id=explanation_id,
                analysis_id=analysis_id,
                answer_text=answer_text,
                verification=_deterministic_verification(answer_text, decision),
                model_name=(answer.get("meta") or {}).get("model"),
                output_hash=stable_hash(answer_text),
            )
        self._publish(
            analysis_id,
            "ExplanationGenerated",
            request_id=request_id,
            payload={
                "explanation_id": explanation_id,
                "answer_hash": stable_hash(answer_text),
                "answer_chars": len(answer_text),
            },
            component="explanation",
        )

        self._publish(
            analysis_id,
            "ResponseReadModelCreated",
            request_id=request_id,
            payload={"compatibility_endpoint": "/api/interactions/analyze"},
            component="api",
        )
        if self.event_store is not None:
            self.event_store.update_analysis_status(analysis_id, "completed")

        return MedicationSetAnalysis(
            analysis_id=analysis_id,
            request_id=request_id,
            medications=concepts,
            evidence_plan=evidence_plan,
            executed_pair=executed_pair,
            rag_output=rag_output,
            evidence_cards=evidence_cards,
            mechanism_graph=mechanism_graph,
            decision=decision,
            events=self.event_bus.events,
        )

    def _publish(self, analysis_id: str, event_type: str, **kwargs: Any) -> None:
        self.event_bus.publish(DomainEvent.create(analysis_id, event_type, **kwargs))


def _default_event_store(settings: Settings) -> SQLiteEventStore | None:
    if not settings.enable_event_store:
        return None
    return SQLiteEventStore(settings.sqlite_cache_path)


def _evidence_cards_from_context(analysis_id: str, context: dict[str, Any], pair: list[str]) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    signals = context.get("signals") or {}
    tabular = signals.get("tabular") or {}
    faers = signals.get("faers") or {}
    pkpd = context.get("pkpd") or {}

    canonical = ((pkpd.get("pk_detail") or {}).get("canonical_interaction") or {})
    if canonical:
        claim = str(canonical.get("mechanism") or canonical)
        cards.append(
            _card(
                analysis_id,
                "Canonical PK/PD",
                "curated_dictionary",
                "mechanism",
                claim,
                "mechanistic",
                pair,
                payload=canonical,
            )
        )

    prr = tabular.get("prr")
    if prr is not None:
        cards.append(
            _card(
                analysis_id,
                "TWOSIDES",
                "local_signal_dataset",
                "adverse_signal",
                f"Pair reporting ratio signal for {' + '.join(pair)}: {prr}",
                "pharmacovigilance_signal",
                pair,
                payload={"prr": prr},
                limitations=["Associative signal only; not incidence or causality."],
            )
        )

    if any(faers.get(key) for key in ("top_reactions_a", "top_reactions_b", "combo_reactions")):
        cards.append(
            _card(
                analysis_id,
                "OpenFDA FAERS",
                "public_api",
                "adverse_signal",
                "FAERS reaction rows were returned for this medication set.",
                "pharmacovigilance_signal",
                pair,
                payload=faers,
                limitations=["FAERS reports are spontaneous reports and cannot establish causality or incidence."],
            )
        )

    pk_summary = str(pkpd.get("pk_summary") or "").strip()
    pd_summary = str(pkpd.get("pd_summary") or "").strip()
    if pk_summary:
        cards.append(
            _card(analysis_id, "PK/PD engine", "mechanism_engine", "pk_mechanism", pk_summary, "mechanistic", pair)
        )
    if pd_summary:
        cards.append(
            _card(analysis_id, "PK/PD engine", "mechanism_engine", "pd_mechanism", pd_summary, "mechanistic", pair)
        )
    return cards


def _card(
    analysis_id: str,
    source_name: str,
    source_type: str,
    claim_type: str,
    claim_text: str,
    evidence_grade: str,
    pair: list[str],
    *,
    payload: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=evidence_id_for(analysis_id, source_name, claim_type, claim_text),
        analysis_id=analysis_id,
        source_name=source_name,
        source_type=source_type,
        claim_type=claim_type,
        claim_text=claim_text,
        evidence_grade=evidence_grade,  # type: ignore[arg-type]
        drug_scope=pair,
        payload=payload or {},
        limitations=limitations or [],
    )


def _mechanism_graph_from_context(
    context: dict[str, Any],
    cards: list[EvidenceCard],
    pair: list[str],
) -> MechanismGraph:
    nodes = [
        MechanismNode(id=f"drug:{pair[0]}", label=pair[0], type="drug"),
        MechanismNode(id=f"drug:{pair[1]}", label=pair[1], type="drug"),
    ]
    edges: list[MechanismEdge] = []
    clusters: list[MechanismCluster] = []

    pkpd = context.get("pkpd") or {}
    pk_detail = pkpd.get("pk_detail") or {}
    canonical = pk_detail.get("canonical_interaction") or {}
    evidence_ids = [card.evidence_id for card in cards if card.claim_type in {"mechanism", "pk_mechanism"}]
    if canonical or pkpd.get("pk_summary"):
        mechanism_id = "mechanism:pk"
        nodes.append(MechanismNode(id=mechanism_id, label="PK mechanism", type="mechanism", payload=pk_detail))
        edges.append(MechanismEdge(source=f"drug:{pair[1]}", target=mechanism_id, type="drives", confidence="medium", evidence_ids=evidence_ids))
        edges.append(MechanismEdge(source=mechanism_id, target=f"drug:{pair[0]}", type="affects", confidence="medium", evidence_ids=evidence_ids))
        clusters.append(
            MechanismCluster(
                cluster_id="cluster:pk",
                label="PK interaction mechanism",
                risk_type="PK",
                drivers=[pair[1]],
                affected_drugs=[pair[0]],
                confidence="medium",
                evidence_ids=evidence_ids,
            )
        )

    pd_detail = pkpd.get("pd_detail") or {}
    if pd_detail.get("overlap_targets") or pd_detail.get("overlap_pathways"):
        mechanism_id = "mechanism:pd"
        nodes.append(MechanismNode(id=mechanism_id, label="PD overlap", type="mechanism", payload=pd_detail))
        for drug in pair:
            edges.append(MechanismEdge(source=f"drug:{drug}", target=mechanism_id, type="contributes", confidence="low"))

    return MechanismGraph(nodes=nodes, edges=edges, clusters=clusters)


def _decision_from_context(
    analysis_id: str,
    context: dict[str, Any],
    cards: list[EvidenceCard],
    pair: list[str],
) -> InteractionDecision:
    risk_level, confidence = _risk_and_confidence(context)
    evidence_grade = _highest_evidence_grade(cards)
    pkpd = context.get("pkpd") or {}
    mechanisms = []
    if pkpd.get("pk_summary"):
        mechanisms.append({"type": "PK", "summary": pkpd["pk_summary"]})
    if pkpd.get("pd_summary"):
        mechanisms.append({"type": "PD", "summary": pkpd["pd_summary"]})

    source_limitations = []
    for card in cards:
        source_limitations.extend(card.limitations)
    source_limitations.extend(context.get("caveats") or [])

    return InteractionDecision(
        analysis_id=analysis_id,
        interaction_scope="pair",
        drugs=pair,
        risk_level=risk_level,
        confidence=confidence,
        evidence_grade=evidence_grade,
        mechanisms=mechanisms,
        recommended_action=_recommended_action(risk_level),
        monitoring=_monitoring_hints(context),
        missing_patient_factors=["renal function", "hepatic function", "baseline ECG/QTc", "current INR"],
        source_limitations=list(dict.fromkeys(str(item) for item in source_limitations if str(item).strip()))[:12],
        abstention_reason=None if risk_level != "insufficient_evidence" else "No usable evidence returned by active sources.",
    )


def _risk_and_confidence(context: dict[str, Any]) -> tuple[str, str]:
    signals = context.get("signals") or {}
    tabular = signals.get("tabular") or {}
    faers = signals.get("faers") or {}
    pkpd = context.get("pkpd") or {}
    pk_detail = pkpd.get("pk_detail") or {}

    score = 0
    if pk_detail.get("canonical_interaction"):
        score += 3
    if any((pk_detail.get("overlaps") or {}).values()):
        score += 2
    if faers.get("combo_reactions"):
        score += 1
    try:
        prr = float(tabular.get("prr"))
        if prr > 2:
            score += 2
        elif prr > 1.5:
            score += 1
    except Exception:
        pass

    if score >= 4:
        return "major", "high"
    if score >= 2:
        return "moderate", "medium"
    if score > 0:
        return "theoretical", "low"
    return "insufficient_evidence", "low"


def _highest_evidence_grade(cards: list[EvidenceCard]) -> str:
    order = [
        "label",
        "guideline",
        "clinical_study",
        "pk_study",
        "mechanistic",
        "pharmacovigilance_signal",
        "inferred_hypothesis",
        "insufficient",
    ]
    grades = {card.evidence_grade for card in cards}
    for grade in order:
        if grade in grades:
            return grade
    return "insufficient"


def _recommended_action(risk_level: str) -> str:
    if risk_level in {"contraindicated", "avoid"}:
        return "avoid"
    if risk_level == "major":
        return "specialist_review"
    if risk_level == "moderate":
        return "monitor"
    if risk_level in {"minor", "theoretical"}:
        return "counsel"
    return "insufficient_evidence"


def _monitoring_hints(context: dict[str, Any]) -> list[str]:
    pkpd = context.get("pkpd") or {}
    text = " ".join(str(pkpd.get(key) or "") for key in ("pk_summary", "pd_summary")).lower()
    hints = []
    if "warfarin" in text or "inr" in text or "anticoagul" in text:
        hints.append("INR and bleeding signs")
    if "qt" in text:
        hints.append("ECG/QTc and electrolytes")
    if "hepato" in text or "liver" in text:
        hints.append("ALT/AST and hepatic symptoms")
    return hints


def _deterministic_verification(answer_text: str, decision: InteractionDecision) -> dict[str, Any]:
    text = answer_text.lower()
    unsupported_dose_language = any(term in text for term in ("reduce the dose", "dose reduction", "increase the dose"))
    return {
        "checked": True,
        "risk_level": decision.risk_level,
        "contains_dose_language": unsupported_dose_language,
        "note": "Deterministic first-pass verification; model verifier can be added after schema stabilization.",
    }
