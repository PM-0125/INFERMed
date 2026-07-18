from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from src.application.commands import AnalyzeMedicationSetCommand
from src.application.event_bus import EventBus
from src.application.events import DomainEvent, stable_hash
from src.application.interaction_modeling import build_drug_profile_graph, build_interaction_reasoning_record
from src.application.tool_planner import EvidencePlan, ToolPlanner
from src.config.settings import Settings, get_settings
from src.domain.decision.entities import InteractionDecision
from src.domain.decision.rules import (
    missing_patient_factors_for_decision,
    patient_amplifiers as patient_amplifiers_for_decision,
    recommended_action_for_risk,
)
from src.domain.decision.scoring import score_pair_context
from src.domain.evidence.entities import EvidenceCard, evidence_id_for
from src.domain.evidence.hierarchy import rank_evidence_cards, strongest_evidence_grade
from src.domain.explanation.verifier import ExplanationVerifier
from src.domain.mechanism.engines import detect_pd_mechanisms, detect_pk_mechanisms
from src.domain.mechanism.entities import MechanismCluster, MechanismEdge, MechanismGraph, MechanismNode
from src.domain.medication.entities import MedicationConcept
from src.domain.medication.services import medication_set_key, normalize_medication
from src.domain.patient.services import normalize_patient_context
from src.domain.profile.entities import DrugProfileGraph
from src.domain.reasoning.services import MedicationSetReasoner
from src.domain.reasoning.entities import InteractionReasoningRecord
from src.domain.research.services import ResearchHypothesisBuilder
from src.domain.safety.entities import SafetyReport
from src.domain.safety.services import ZeroTrustSafetyGate
from src.infrastructure.persistence.event_store import SQLiteEventStore

RagRunner = Callable[..., dict[str, Any]]
ContextRunner = Callable[..., dict[str, Any]]
AnswerGenerator = Callable[..., dict[str, Any]]
ProgressCallback = Callable[[str, str, dict[str, Any]], None]

log = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class PairContextResult:
    pair: tuple[str, str]
    context: dict[str, Any]
    answer: dict[str, Any] | None = None
    evidence_cards: list[EvidenceCard] | None = None
    decision: InteractionDecision | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": list(self.pair),
            "context_meta": self.context.get("meta") or {},
            "sources": self.context.get("sources") or {},
            "decision": self.decision.to_dict() if self.decision else None,
            "evidence_card_count": len(self.evidence_cards or []),
        }


@dataclass(frozen=True)
class MedicationSetAnalysis:
    analysis_id: str
    request_id: str | None
    medications: list[MedicationConcept]
    evidence_plan: EvidencePlan
    executed_pair: tuple[str, str]
    executed_pairs: list[tuple[str, str]]
    rag_output: dict[str, Any]
    pair_results: list[PairContextResult]
    aggregate_context: dict[str, Any]
    evidence_cards: list[EvidenceCard]
    mechanism_graph: MechanismGraph
    profile_graph: DrugProfileGraph
    reasoning_record: InteractionReasoningRecord
    decision: InteractionDecision
    safety_report: SafetyReport
    ndrug_summary: dict[str, Any]
    events: list[DomainEvent]

    def to_read_model(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "analysisId": self.analysis_id,
            "request_id": self.request_id,
            "normalized_medications": [item.to_dict() for item in self.medications],
            "normalizedMedications": [item.to_dict() for item in self.medications],
            "executed_pair": list(self.executed_pair),
            "executedPair": list(self.executed_pair),
            "executed_pairs": [list(pair) for pair in self.executed_pairs],
            "executedPairs": [list(pair) for pair in self.executed_pairs],
            "pair_results": [item.to_dict() for item in self.pair_results],
            "pairResults": [item.to_dict() for item in self.pair_results],
            "evidence_plan": self.evidence_plan.to_dict(),
            "evidencePlan": self.evidence_plan.to_dict(),
            "decision": self.decision.to_dict(),
            "mechanism_graph": self.mechanism_graph.to_dict(),
            "mechanismGraph": self.mechanism_graph.to_dict(),
            "drug_profile_graph": self.profile_graph.to_dict(),
            "drugProfileGraph": self.profile_graph.to_dict(),
            "reasoning_record": self.reasoning_record.to_dict(),
            "reasoningRecord": self.reasoning_record.to_dict(),
            "safety_report": self.safety_report.to_dict(),
            "safetyReport": self.safety_report.to_dict(),
            "ndrug_reasoning": self.ndrug_summary,
            "ndrugReasoning": self.ndrug_summary,
            "domain_events": [event.to_dict() for event in self.events],
        }


class AnalyzeMedicationSetUseCase:
    """Medication-set analysis use case with pair-context compatibility.

    Production callers should provide `context_runner` and `answer_generator`.
    That lets the use case retrieve all pair contexts but spend only one final
    LLM call for the whole medication set. Tests and older callers can still
    provide only `rag_runner`; in that mode the first pair answer is reused.
    """

    def __init__(
        self,
        *,
        rag_runner: RagRunner,
        context_runner: ContextRunner | None = None,
        answer_generator: AnswerGenerator | None = None,
        settings: Settings | None = None,
        event_store: SQLiteEventStore | None = None,
        tool_planner: ToolPlanner | None = None,
        safety_gate: ZeroTrustSafetyGate | None = None,
        progress_callback: ProgressCallback | None = None,
    ):
        self.rag_runner = rag_runner
        self.context_runner = context_runner
        self.answer_generator = answer_generator
        self.settings = settings or get_settings()
        self.event_store = event_store if event_store is not None else _default_event_store(self.settings)
        self.event_bus = EventBus(self.event_store)
        self.tool_planner = tool_planner or ToolPlanner()
        self.safety_gate = safety_gate or ZeroTrustSafetyGate()
        self.explanation_verifier = ExplanationVerifier()
        self.progress_callback = progress_callback

    def execute(self, command: AnalyzeMedicationSetCommand) -> MedicationSetAnalysis:
        analysis_id = "ana_" + uuid.uuid4().hex
        request_id = command.request_id or "req_" + uuid.uuid4().hex

        concepts = [normalize_medication(item) for item in command.medications]
        concepts = [item for item in concepts if item.normalized_name]
        if len(concepts) < 2:
            raise ValueError("At least two non-empty medications are required")
        patient_context = normalize_patient_context(command.patient_context)

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
        self._progress(
            "analysis_requested",
            f"Received {len(concepts)} medication inputs.",
            {"analysis_id": analysis_id, "medication_count": len(concepts), "audience": command.audience},
        )

        for concept in concepts:
            self._publish(
                analysis_id,
                "MedicationNormalized",
                request_id=request_id,
                payload=concept.to_dict(),
                component="medication_identity",
            )
        if command.patient_context is not None:
            self._publish(
                analysis_id,
                "PatientContextReceived",
                request_id=request_id,
                payload=patient_context.to_dict(),
                component="patient_context",
            )
        self._progress(
            "medications_normalized",
            "Normalized medication names into analysis concepts.",
            {"medications": [concept.normalized_name for concept in concepts]},
        )

        evidence_plan = self.tool_planner.create_plan(concepts, data_mode=self.settings.data_mode)
        self._publish(
            analysis_id,
            "EvidencePlanCreated",
            request_id=request_id,
            payload=evidence_plan.to_dict(),
            component="tool_planner",
        )
        self._progress(
            "evidence_plan_created",
            f"Planned {evidence_plan.pair_count} pair context fetches.",
            {"pair_count": evidence_plan.pair_count, "tools": [tool.name for tool in evidence_plan.tools]},
        )

        pair_results = self._fetch_pair_contexts(
            analysis_id=analysis_id,
            request_id=request_id,
            pairs=evidence_plan.priority_pairs,
            command=command,
        )
        executed_pairs = [item.pair for item in pair_results]
        executed_pair = executed_pairs[0]
        aggregate_context = build_medication_set_context_from_pair_results(
            analysis_id=analysis_id,
            medications=[concept.normalized_name for concept in concepts],
            pair_results=pair_results,
            data_mode=self.settings.data_mode,
            patient_context=patient_context.to_dict() if command.patient_context is not None else None,
        )
        self._progress(
            "medication_set_context_built",
            "Built medication-set evidence context and pair risk ranking.",
            {
                "pair_count": len(pair_results),
                "top_pairs": (aggregate_context.get("medication_set") or {}).get("top_pairs", [])[:5],
            },
        )

        answer = self._generate_medication_set_answer(
            aggregate_context,
            command=command,
            fallback_answer=pair_results[0].answer or {},
        )
        rag_output = {"context": aggregate_context, "answer": answer}
        context = aggregate_context
        answer_text = str(answer.get("text") or "").strip()

        self._publish(
            analysis_id,
            "SourceFetchSucceeded",
            request_id=request_id,
            payload={
                "pair_count": len(pair_results),
                "executed_pairs": [list(pair) for pair in executed_pairs],
                "context_hash": stable_hash(context),
                "source_count": len(context.get("source_status") or []),
            },
            component="evidence",
            source_tool_name="current_pair_rag_pipeline",
        )

        evidence_cards = rank_evidence_cards(_aggregate_evidence_cards(analysis_id, pair_results, [concept.normalized_name for concept in concepts]))
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

        mechanism_graph = _mechanism_graph_from_pair_results(pair_results, evidence_cards, [concept.normalized_name for concept in concepts])
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

        profile_graph = _profile_graph_from_pair_results(
            analysis_id=analysis_id,
            drugs=[concept.normalized_name for concept in concepts],
            pair_results=pair_results,
            evidence_cards=evidence_cards,
        )
        self._publish(
            analysis_id,
            "DrugProfileGraphBuilt",
            request_id=request_id,
            payload={
                "node_count": len(profile_graph.nodes),
                "edge_count": len(profile_graph.edges),
                "drugs_with_gaps": len(profile_graph.missing_elements),
            },
            component="profile",
        )

        reasoning_record = build_interaction_reasoning_record(
            analysis_id=analysis_id,
            drugs=[concept.normalized_name for concept in concepts],
            profile_graph=profile_graph,
            context=context,
            evidence_cards=evidence_cards,
        )
        self._publish(
            analysis_id,
            "InteractionReasoningRecordBuilt",
            request_id=request_id,
            payload={
                "known_status": reasoning_record.known_status,
                "signal_count": len(reasoning_record.signals),
                "hypothesis_count": len(reasoning_record.hypotheses),
            },
            component="reasoning",
        )
        ndrug_summary = MedicationSetReasoner().summarize(
            drugs=[concept.normalized_name for concept in concepts],
            pair_summaries=(context.get("medication_set") or {}).get("evaluated_pairs") or [],
            mechanism_graph=mechanism_graph,
            profile_graph=profile_graph,
            reasoning_record=reasoning_record,
        ).to_dict()
        research_signals = [
            signal.to_dict()
            for signal in ResearchHypothesisBuilder().build(context=context, profile_graph=profile_graph)
        ]
        ndrug_summary["research_signals"] = research_signals
        context["medication_set"]["reasoning_summary"] = ndrug_summary
        self._publish(
            analysis_id,
            "NDrugReasoningSummaryBuilt",
            request_id=request_id,
            payload={
                "pair_count": ndrug_summary.get("pair_count"),
                "cluster_count": len(ndrug_summary.get("clusters") or []),
                "hypothesis_count": len(ndrug_summary.get("hypotheses") or []),
                "research_signal_count": len(research_signals),
            },
            component="reasoning",
        )

        decision = _decision_from_medication_set_context(
            analysis_id,
            context,
            evidence_cards,
            [concept.normalized_name for concept in concepts],
            pair_results,
        )
        self._publish(
            analysis_id,
            "ClinicalDecisionCreated",
            request_id=request_id,
            payload=decision.to_dict(),
            component="decision",
        )

        safety_report = self.safety_gate.evaluate(
            analysis_id=analysis_id,
            answer_text=answer_text,
            reasoning_record=reasoning_record,
            evidence_cards=evidence_cards,
        )
        self._publish(
            analysis_id,
            "SafetyReportCreated",
            request_id=request_id,
            payload={
                "requires_review": safety_report.requires_review,
                "finding_count": len(safety_report.findings),
            },
            component="safety",
        )

        explanation_id = "exp_" + stable_hash({"analysis_id": analysis_id, "answer": answer_text})[:16]
        if self.event_store is not None:
            self.event_store.append_explanation_record(
                explanation_id=explanation_id,
                analysis_id=analysis_id,
                answer_text=answer_text,
                verification=_deterministic_verification(answer_text, decision, safety_report, evidence_cards),
                model_name=(answer.get("meta") or {}).get("model"),
                output_hash=stable_hash(answer_text),
            )
            self.event_store.append_analysis_snapshot(
                analysis_id=analysis_id,
                context=context,
                decision=decision.to_dict(),
                response_read_model={
                    "answer": answer,
                    "pair_results": [item.to_dict() for item in pair_results],
                },
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
            executed_pairs=executed_pairs,
            rag_output=rag_output,
            pair_results=pair_results,
            aggregate_context=aggregate_context,
            evidence_cards=evidence_cards,
            mechanism_graph=mechanism_graph,
            profile_graph=profile_graph,
            reasoning_record=reasoning_record,
            decision=decision,
            safety_report=safety_report,
            ndrug_summary=ndrug_summary,
            events=self.event_bus.events,
        )

    def _publish(self, analysis_id: str, event_type: str, **kwargs: Any) -> None:
        event = DomainEvent.create(analysis_id, event_type, **kwargs)
        payload = kwargs.get("payload") or {}
        log.info(
            "INFERMed lifecycle event: analysis_id=%s event=%s component=%s status=%s source_tool=%s payload_keys=%s",
            analysis_id,
            event_type,
            kwargs.get("component") or "",
            kwargs.get("status") or "",
            kwargs.get("source_tool_name") or "",
            sorted(payload.keys()) if isinstance(payload, dict) else [],
        )
        self.event_bus.publish(event)

    def _progress(self, stage: str, message: str, payload: dict[str, Any] | None = None) -> None:
        safe_payload = payload or {}
        log.info(
            "INFERMed progress: stage=%s message=%s payload_keys=%s",
            stage,
            message,
            sorted(safe_payload.keys()) if isinstance(safe_payload, dict) else [],
        )
        if self.progress_callback is not None:
            self.progress_callback(stage, message, safe_payload)

    def _fetch_pair_contexts(
        self,
        *,
        analysis_id: str,
        request_id: str,
        pairs: list[tuple[str, str]],
        command: AnalyzeMedicationSetCommand,
    ) -> list[PairContextResult]:
        results: list[PairContextResult] = []
        for index, pair in enumerate(pairs, start=1):
            self._publish(
                analysis_id,
                "SourceFetchRequested",
                request_id=request_id,
                payload={"pair": list(pair), "tool": "pair_context_pipeline"},
                component="evidence",
                source_tool_name="pair_context_pipeline",
                status="requested",
            )
            self._progress(
                "source_fetch_started",
                f"Collecting evidence for {pair[0]} + {pair[1]} ({index}/{len(pairs)}).",
                {"pair": list(pair), "index": index, "pair_count": len(pairs)},
            )
            try:
                if self.context_runner is not None:
                    context = self.context_runner(
                        pair[0],
                        pair[1],
                        mode=command.audience,
                        force_refresh=command.refresh_evidence,
                    )
                    answer = None
                else:
                    rag_output = self.rag_runner(
                        pair[0],
                        pair[1],
                        mode=command.audience,
                        use_cache_context=not command.refresh_evidence,
                        use_cache_response=False,
                        model_name=None,
                    )
                    context = rag_output.get("context") or {}
                    answer = rag_output.get("answer") or {}
            except Exception as exc:
                self._publish(
                    analysis_id,
                    "SourceFetchFailed",
                    request_id=request_id,
                    payload={"pair": list(pair)},
                    component="evidence",
                    source_tool_name="pair_context_pipeline",
                    status="failed",
                    error_summary=str(exc),
                )
                self._progress(
                    "source_fetch_failed",
                    f"Evidence collection failed for {pair[0]} + {pair[1]}.",
                    {"pair": list(pair), "error": str(exc)},
                )
                if self.event_store is not None:
                    self.event_store.update_analysis_status(analysis_id, "failed")
                raise

            cards = _evidence_cards_from_context(analysis_id, context, list(pair))
            decision = _decision_from_context(analysis_id, context, cards, list(pair))
            results.append(PairContextResult(pair=pair, context=context, answer=answer, evidence_cards=cards, decision=decision))
            self._progress(
                "source_fetch_completed",
                f"Normalized evidence for {pair[0]} + {pair[1]}.",
                {
                    "pair": list(pair),
                    "risk_level": decision.risk_level,
                    "confidence": decision.confidence,
                    "evidence_card_count": len(cards),
                },
            )

        if not results:
            raise ValueError("No medication pairs were available for analysis")
        return results

    def _generate_medication_set_answer(
        self,
        context: dict[str, Any],
        *,
        command: AnalyzeMedicationSetCommand,
        fallback_answer: dict[str, Any],
    ) -> dict[str, Any]:
        if self.answer_generator is None:
            return fallback_answer or {"text": "No generated answer was returned."}
        self._progress(
            "answer_generation_started",
            "Generating one evidence-grounded explanation for the medication set.",
            {"strategy": "single_final_model_call"},
        )
        answer = self.answer_generator(context, command.audience, patient_context=command.patient_context)
        self._progress(
            "answer_generation_completed",
            "Generated the medication-set explanation.",
            {"answer_chars": len(str(answer.get("text") or ""))},
        )
        return answer


def build_medication_set_context_from_pair_results(
    *,
    analysis_id: str,
    medications: list[str],
    pair_results: list[PairContextResult],
    data_mode: str,
    patient_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one compact context for a whole medication set.

    The legacy prompt stack still expects a pair-like `drugs.a` / `drugs.b`
    shape, so the highest-ranked pair remains there for compatibility while
    all N-drug information is exposed under `medication_set`.
    """

    ranked = sorted(pair_results, key=lambda item: _risk_rank(item.decision.risk_level if item.decision else "insufficient_evidence"), reverse=True)
    primary = ranked[0]
    primary_context = primary.context
    all_caveats = _unique(
        caveat
        for result in pair_results
        for caveat in ((result.context.get("caveats") or []) + _decision_limitations(result.decision))
    )
    source_status = _merge_source_status([result.context for result in pair_results])
    sources = _merge_sources([result.context for result in pair_results])
    pair_summaries = [_pair_summary(result) for result in ranked]

    top_pair = primary.pair
    top_context_drugs = primary_context.get("drugs") or {}
    drug_identities = _medication_set_drug_identities(medications, pair_results)

    aggregate = {
        "drugs": {
            "a": top_context_drugs.get("a") or {"name": top_pair[0]},
            "b": top_context_drugs.get("b") or {"name": top_pair[1]},
            "set": drug_identities,
        },
        "medication_set": {
            "analysis_id": analysis_id,
            "drugs": medications,
            "drug_count": len(medications),
            "pair_count": len(pair_results),
            "evaluated_pairs": pair_summaries,
            "top_pairs": pair_summaries[: min(8, len(pair_summaries))],
            "patient_context": patient_context or {},
            "generation_strategy": "all_pair_contexts_single_final_answer",
        },
        "signals": _aggregate_signals(primary_context, pair_results),
        "pkpd": _aggregate_pkpd(primary_context, pair_summaries),
        "caveats": all_caveats,
        "source_status": source_status,
        "sources": sources,
        "meta": {
            "analysis_id": analysis_id,
            "analysis_scope": "medication_set" if len(medications) > 2 else "pair",
            "drug_set": medications,
            "primary_pair": list(top_pair),
            "pair_count": len(pair_results),
            "data_mode": data_mode,
            "version": (primary_context.get("meta") or {}).get("version"),
            "llm_generation_strategy": "single_final_answer",
        },
    }
    return aggregate


def evidence_cards_from_context(analysis_id: str, context: dict[str, Any], pair: list[str]) -> list[EvidenceCard]:
    return _evidence_cards_from_context(analysis_id, context, pair)


def decision_from_context(
    analysis_id: str,
    context: dict[str, Any],
    cards: list[EvidenceCard],
    pair: list[str],
) -> InteractionDecision:
    return _decision_from_context(analysis_id, context, cards, pair)


def _aggregate_evidence_cards(
    analysis_id: str,
    pair_results: list[PairContextResult],
    medications: list[str],
) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    for result in pair_results:
        cards.extend(result.evidence_cards or [])
        if result.decision is not None:
            cards.append(
                _card(
                    analysis_id,
                    "INFERMed pair reasoner",
                    "reasoning_engine",
                    "pair_decision",
                    (
                        f"{result.pair[0]} + {result.pair[1]} classified as "
                        f"{result.decision.risk_level} with {result.decision.confidence} confidence."
                    ),
                    result.decision.evidence_grade,
                    list(result.pair),
                    payload=result.decision.to_dict(),
                    limitations=result.decision.source_limitations,
                )
            )
    if len(medications) > 2:
        cards.append(
            _card(
                analysis_id,
                "INFERMed medication-set reasoner",
                "reasoning_engine",
                "medication_set_summary",
                f"Evaluated {len(pair_results)} pair combinations across {len(medications)} medications.",
                "inferred_hypothesis",
                medications,
                payload={"pair_count": len(pair_results), "medications": medications},
                limitations=[
                    "N-drug cluster reasoning summarizes pair and shared-profile evidence; it is not a substitute for patient-specific clinical judgment."
                ],
            )
        )
    return _dedupe_cards(cards)


def _mechanism_graph_from_pair_results(
    pair_results: list[PairContextResult],
    cards: list[EvidenceCard],
    medications: list[str],
) -> MechanismGraph:
    nodes_by_id: dict[str, MechanismNode] = {}
    edges_by_key: dict[tuple[str, str, str], MechanismEdge] = {}
    clusters_by_id: dict[str, MechanismCluster] = {}

    for drug in medications:
        node = MechanismNode(id=f"drug:{drug}", label=drug, type="drug")
        nodes_by_id[node.id] = node

    for result in pair_results:
        graph = _mechanism_graph_from_context(result.context, result.evidence_cards or cards, list(result.pair))
        suffix = _pair_slug(result.pair)
        for node in graph.nodes:
            node_id = node.id if node.type == "drug" else f"{node.id}:{suffix}"
            nodes_by_id.setdefault(node_id, MechanismNode(id=node_id, label=node.label, type=node.type, payload={**node.payload, "pair": list(result.pair)}))
        for edge in graph.edges:
            source = edge.source if edge.source.startswith("drug:") else f"{edge.source}:{suffix}"
            target = edge.target if edge.target.startswith("drug:") else f"{edge.target}:{suffix}"
            edges_by_key.setdefault((source, target, edge.type), MechanismEdge(source=source, target=target, type=edge.type, confidence=edge.confidence, evidence_ids=edge.evidence_ids))
        for cluster in graph.clusters:
            cluster_id = f"{cluster.cluster_id}:{suffix}"
            clusters_by_id.setdefault(
                cluster_id,
                MechanismCluster(
                    cluster_id=cluster_id,
                    label=f"{cluster.label}: {result.pair[0]} + {result.pair[1]}",
                    risk_type=cluster.risk_type,
                    drivers=cluster.drivers,
                    affected_drugs=cluster.affected_drugs,
                    confidence=cluster.confidence,
                    evidence_ids=cluster.evidence_ids,
                ),
            )

    shared_cards = [card.evidence_id for card in cards if card.claim_type == "medication_set_summary"]
    if len(medications) > 2:
        clusters_by_id["cluster:medication_set"] = MechanismCluster(
            cluster_id="cluster:medication_set",
            label="Medication-set cluster review",
            risk_type="N_DRUG",
            drivers=medications,
            affected_drugs=medications,
            confidence="medium",
            evidence_ids=shared_cards,
        )
    return MechanismGraph(nodes=list(nodes_by_id.values()), edges=list(edges_by_key.values()), clusters=list(clusters_by_id.values()))


def _profile_graph_from_pair_results(
    *,
    analysis_id: str,
    drugs: list[str],
    pair_results: list[PairContextResult],
    evidence_cards: list[EvidenceCard],
) -> DrugProfileGraph:
    node_rows: dict[str, Any] = {}
    edge_rows: dict[tuple[str, str, str], Any] = {}
    missing: dict[str, set[str]] = {drug: set() for drug in drugs}
    limitations: list[str] = []

    for result in pair_results:
        graph = build_drug_profile_graph(
            analysis_id=analysis_id,
            drugs=list(result.pair),
            context=result.context,
            evidence_cards=result.evidence_cards or evidence_cards,
        )
        for node in graph.nodes:
            existing = node_rows.get(node.node_id)
            if existing is None:
                node_rows[node.node_id] = node
            else:
                node_rows[node.node_id] = existing.__class__(
                    node_id=existing.node_id,
                    type=existing.type,
                    label=existing.label,
                    drug_scope=_unique([*existing.drug_scope, *node.drug_scope]),
                    evidence_ids=_unique([*existing.evidence_ids, *node.evidence_ids]),
                    payload={**existing.payload, **node.payload},
                )
        for edge in graph.edges:
            edge_rows.setdefault((edge.source_id, edge.target_id, edge.type), edge)
        for drug, gaps in graph.missing_elements.items():
            missing.setdefault(drug, set()).update(gaps)
        limitations.extend(graph.source_limitations)

    return DrugProfileGraph(
        analysis_id=analysis_id,
        drugs=drugs,
        nodes=list(node_rows.values()),
        edges=list(edge_rows.values()),
        missing_elements={drug: sorted(gaps) for drug, gaps in missing.items() if gaps},
        source_limitations=_unique(limitations),
    )


def _decision_from_medication_set_context(
    analysis_id: str,
    context: dict[str, Any],
    cards: list[EvidenceCard],
    medications: list[str],
    pair_results: list[PairContextResult],
) -> InteractionDecision:
    ranked = sorted(pair_results, key=lambda item: _risk_rank(item.decision.risk_level if item.decision else "insufficient_evidence"), reverse=True)
    top_decision = ranked[0].decision if ranked else None
    if len(medications) <= 2 and top_decision is not None:
        return top_decision

    risk_level = top_decision.risk_level if top_decision else "insufficient_evidence"
    confidence = _medication_set_confidence([item.decision for item in pair_results if item.decision])
    mechanisms = []
    for result in ranked[:8]:
        if result.decision is None:
            continue
        mechanisms.append(
            {
                "type": "pair_summary",
                "pair": list(result.pair),
                "risk_level": result.decision.risk_level,
                "confidence": result.decision.confidence,
                "mechanisms": result.decision.mechanisms[:4],
            }
        )
    limitations = _unique(
        item
        for card in cards
        for item in card.limitations
    )
    limitations.extend(context.get("caveats") or [])
    patient_context_payload = (context.get("medication_set") or {}).get("patient_context") or None
    patient_context = normalize_patient_context(patient_context_payload) if patient_context_payload else None
    return InteractionDecision(
        analysis_id=analysis_id,
        interaction_scope="medication_set",
        drugs=medications,
        risk_level=risk_level,
        confidence=confidence,
        evidence_grade=_highest_evidence_grade(cards),
        mechanisms=mechanisms,
        patient_amplifiers=patient_amplifiers_for_decision(patient_context),
        recommended_action=_recommended_action(risk_level),
        monitoring=_unique(item for result in pair_results if result.decision for item in result.decision.monitoring),
        missing_patient_factors=[
            *missing_patient_factors_for_decision(patient_context),
            "full medication list with dose/route",
        ],
        what_would_change_the_answer=[
            "Patient-specific renal/hepatic impairment",
            "Baseline INR or bleeding history",
            "Baseline QTc/electrolyte abnormalities",
            "Pharmacogenomic variants affecting metabolism",
            "Dose, route, and timing of each medication",
        ],
        source_limitations=list(dict.fromkeys(str(item) for item in limitations if str(item).strip()))[:16],
        abstention_reason=None if risk_level != "insufficient_evidence" else "No usable evidence returned by active sources.",
    )


def _pair_summary(result: PairContextResult) -> dict[str, Any]:
    context = result.context
    pkpd = context.get("pkpd") or {}
    decision = result.decision
    return {
        "pair": list(result.pair),
        "risk_level": decision.risk_level if decision else "insufficient_evidence",
        "confidence": decision.confidence if decision else "low",
        "evidence_grade": decision.evidence_grade if decision else "insufficient",
        "pk_summary": pkpd.get("pk_summary") or "",
        "pd_summary": pkpd.get("pd_summary") or "",
        "sources": context.get("sources") or {},
        "source_limitations": decision.source_limitations if decision else context.get("caveats") or [],
    }


def _aggregate_signals(primary_context: dict[str, Any], pair_results: list[PairContextResult]) -> dict[str, Any]:
    signals = dict(primary_context.get("signals") or {})
    tabular = dict(signals.get("tabular") or {})
    tabular["pair_summaries"] = [_pair_summary(result) for result in pair_results]
    signals["tabular"] = tabular
    signals.setdefault("medication_set", {})
    signals["medication_set"]["pair_count"] = len(pair_results)
    signals["medication_set"]["top_pairs"] = [_pair_summary(result) for result in pair_results[:8]]
    return signals


def _aggregate_pkpd(primary_context: dict[str, Any], pair_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    pkpd = dict(primary_context.get("pkpd") or {})
    lines = []
    for row in pair_summaries[:8]:
        pair = " + ".join(row["pair"])
        pk = _compact(row.get("pk_summary") or "No PK summary", 220)
        pd = _compact(row.get("pd_summary") or "No PD summary", 180)
        lines.append(f"{pair}: risk={row['risk_level']} confidence={row['confidence']}; PK={pk}; PD={pd}")
    if lines:
        pkpd["pk_summary"] = "Medication-set pair PK review: " + " || ".join(lines)
        pkpd["pd_summary"] = "Medication-set pair PD/toxicity review: " + " || ".join(lines)
    return pkpd


def _medication_set_drug_identities(medications: list[str], pair_results: list[PairContextResult]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {drug.lower(): {"name": drug, "ids": {}, "synonyms": []} for drug in medications}
    for result in pair_results:
        for side in ("a", "b"):
            raw = ((result.context.get("drugs") or {}).get(side) or {})
            name = str(raw.get("name") or "").strip().lower()
            if not name:
                continue
            row = by_name.setdefault(name, {"name": name, "ids": {}, "synonyms": []})
            row["ids"].update(raw.get("ids") or {})
            row["synonyms"] = _unique([*(row.get("synonyms") or []), *(raw.get("synonyms") or [])])
    return list(by_name.values())


def _merge_source_status(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for context in contexts:
        for row in context.get("source_status") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            existing = by_name.get(name)
            if existing is None or (row.get("enabled") and not existing.get("enabled")):
                by_name[name] = row
    return list(by_name.values())


def _merge_sources(contexts: list[dict[str, Any]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for context in contexts:
        for key, values in (context.get("sources") or {}).items():
            merged.setdefault(key, [])
            merged[key] = _unique([*merged[key], *[str(item) for item in values or [] if str(item).strip()]])
    return merged


def _dedupe_cards(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    seen: set[str] = set()
    rows: list[EvidenceCard] = []
    for card in cards:
        if card.evidence_id in seen:
            continue
        seen.add(card.evidence_id)
        rows.append(card)
    return rows


def _decision_limitations(decision: InteractionDecision | None) -> list[str]:
    return decision.source_limitations if decision is not None else []


def _risk_rank(risk_level: str) -> int:
    return {
        "contraindicated": 7,
        "avoid": 6,
        "major": 5,
        "moderate": 4,
        "minor": 3,
        "theoretical": 2,
        "no_clinically_significant_interaction_found": 1,
        "insufficient_evidence": 0,
    }.get(str(risk_level), 0)


def _medication_set_confidence(decisions: list[InteractionDecision]) -> str:
    if any(decision.confidence == "high" for decision in decisions):
        return "high"
    if any(decision.confidence == "medium" for decision in decisions):
        return "medium"
    return "low"


def _pair_slug(pair: tuple[str, str]) -> str:
    return "_".join(item.lower().replace(" ", "_") for item in pair)


def _compact(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def _unique(values: Any) -> list[Any]:
    seen: set[str] = set()
    rows: list[Any] = []
    for value in values:
        key = str(value).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(value)
    return rows


def _default_event_store(settings: Settings) -> SQLiteEventStore | None:
    if not settings.enable_event_store:
        return None
    return SQLiteEventStore(settings.sqlite_cache_path)


def _evidence_cards_from_context(analysis_id: str, context: dict[str, Any], pair: list[str]) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    signals = context.get("signals") or {}
    tabular = signals.get("tabular") or {}
    faers = signals.get("faers") or {}
    clinical_reference = signals.get("clinical_reference") or {}
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

    label_rows = clinical_reference.get("openfda_label") or {}
    for side, drug_name in zip(("a", "b"), pair):
        label = label_rows.get(side) or {}
        sections = label.get("sections") or {}
        if not label.get("found") or not sections:
            continue
        claim = f"{drug_name} label sections returned: {', '.join(list(sections.keys())[:5])}"
        cards.append(
            _card(
                analysis_id,
                "openFDA Drug Label",
                "public_label_api",
                "label_context",
                claim,
                "label",
                [drug_name],
                payload=label,
                limitations=label.get("limitations") or ["Label content is product/version specific."],
            )
        )

    rxnorm_rows = clinical_reference.get("rxnorm") or {}
    for side, drug_name in zip(("a", "b"), pair):
        rxnorm = rxnorm_rows.get(side) or {}
        if not rxnorm.get("resolved"):
            continue
        claim = f"{drug_name} resolved to RxCUI {rxnorm.get('rxcui')} ({rxnorm.get('name')})."
        cards.append(
            _card(
                analysis_id,
                "RxNorm/RxClass",
                "public_identity_api",
                "identity_context",
                claim,
                "mechanistic",
                [drug_name],
                payload=rxnorm,
            )
        )

    fda_ref_rows = clinical_reference.get("fda_ddi_reference") or {}
    for side, drug_name in zip(("a", "b"), pair):
        fda_ref = fda_ref_rows.get(side) or {}
        matches = fda_ref.get("matches") or []
        if not matches:
            continue
        cards.append(
            _card(
                analysis_id,
                "FDA CYP/transporter reference",
                "public_reference_snapshot",
                "mechanism_reference",
                f"{drug_name} matched {len(matches)} FDA CYP/transporter table row(s).",
                "mechanistic",
                [drug_name],
                payload=fda_ref,
                limitations=["Reference table examples are not exhaustive and are not patient-specific guidance."],
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

    for graph in (detect_pk_mechanisms(context, cards, pair), detect_pd_mechanisms(context, cards, pair)):
        nodes.extend(graph.nodes)
        edges.extend(graph.edges)
        clusters.extend(graph.clusters)

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
        missing_patient_factors=missing_patient_factors_for_decision(),
        source_limitations=list(dict.fromkeys(str(item) for item in source_limitations if str(item).strip()))[:12],
        abstention_reason=None if risk_level != "insufficient_evidence" else "No usable evidence returned by active sources.",
    )


def _risk_and_confidence(context: dict[str, Any]) -> tuple[str, str]:
    risk_level, confidence, _score = score_pair_context(context)
    return risk_level, confidence


def _highest_evidence_grade(cards: list[EvidenceCard]) -> str:
    return strongest_evidence_grade(cards)


def _recommended_action(risk_level: str) -> str:
    return recommended_action_for_risk(risk_level)


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


def _deterministic_verification(
    answer_text: str,
    decision: InteractionDecision,
    safety_report: SafetyReport,
    evidence_cards: list[EvidenceCard],
) -> dict[str, Any]:
    return ExplanationVerifier().verify(
        answer_text=answer_text,
        decision=decision,
        evidence_cards=evidence_cards,
        safety_report=safety_report,
    ).to_dict()
