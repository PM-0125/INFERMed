from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.domain.decision.scoring import risk_rank
from src.domain.mechanism.entities import MechanismCluster, MechanismGraph
from src.domain.profile.entities import DrugProfileGraph
from src.domain.reasoning.entities import InteractionHypothesis, InteractionReasoningRecord, ReasoningSignal


@dataclass(frozen=True)
class NDrugReasoningSummary:
    pair_count: int
    top_pairs: list[dict[str, Any]] = field(default_factory=list)
    clusters: list[dict[str, Any]] = field(default_factory=list)
    evidence_gaps: dict[str, list[str]] = field(default_factory=dict)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MedicationSetReasoner:
    def summarize(
        self,
        *,
        drugs: list[str],
        pair_summaries: list[dict[str, Any]],
        mechanism_graph: MechanismGraph,
        profile_graph: DrugProfileGraph,
        reasoning_record: InteractionReasoningRecord | None = None,
    ) -> NDrugReasoningSummary:
        top_pairs = sorted(pair_summaries, key=lambda row: risk_rank(row.get("risk_level", "")), reverse=True)[:8]
        clusters = [_cluster_to_summary(cluster) for cluster in mechanism_graph.clusters]
        hypotheses = [hypothesis.to_dict() for hypothesis in (reasoning_record.hypotheses if reasoning_record else [])]
        if not hypotheses and _has_evidence_gap(profile_graph):
            hypotheses = [
                InteractionHypothesis(
                    hypothesis_id="hyp_evidence_gap",
                    mechanism_type="unknown_combination",
                    statement="Potential interaction remains hypothesis-level because required profile elements are missing.",
                    support_level="insufficient",
                    affected_drugs=drugs,
                    limitations=["Missing drug profile elements limit mechanistic certainty."],
                ).to_dict()
            ]
        return NDrugReasoningSummary(
            pair_count=len(pair_summaries),
            top_pairs=top_pairs,
            clusters=clusters,
            evidence_gaps=profile_graph.missing_elements,
            hypotheses=hypotheses,
        )


def reasoning_signal_from_cluster(cluster: MechanismCluster) -> ReasoningSignal:
    support = "supported" if cluster.confidence in {"high", "medium"} else "plausible"
    return ReasoningSignal(
        category="toxicity_convergence" if cluster.risk_type not in {"PK", "PD"} else ("pk_overlap" if cluster.risk_type == "PK" else "pd_overlap"),
        label=cluster.label,
        support_level=support,
        drug_scope=cluster.affected_drugs,
        evidence_ids=cluster.evidence_ids,
        payload=cluster.to_dict() if hasattr(cluster, "to_dict") else asdict(cluster),
    )


def _cluster_to_summary(cluster: MechanismCluster) -> dict[str, Any]:
    return {
        "cluster_id": cluster.cluster_id,
        "label": cluster.label,
        "risk_type": cluster.risk_type,
        "drivers": cluster.drivers,
        "affected_drugs": cluster.affected_drugs,
        "confidence": cluster.confidence,
        "evidence_ids": cluster.evidence_ids,
    }


def _has_evidence_gap(profile_graph: DrugProfileGraph) -> bool:
    return any(profile_graph.missing_elements.values())
