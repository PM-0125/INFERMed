from __future__ import annotations

from src.domain.evidence.entities import EvidenceCard
from src.domain.mechanism.entities import MechanismCluster, MechanismGraph

_TOXICITY_TERMS = {
    "QT": ("qt", "torsade", "arrhythmia"),
    "BLEEDING": ("bleeding", "hemorrhage", "haemorrhage", "inr", "anticoagul"),
    "HEPATIC": ("hepato", "liver", "alt", "ast", "dili"),
    "RENAL": ("renal", "kidney", "nephro"),
    "CNS": ("sedation", "respiratory depression", "cns"),
}


def detect_toxicity_clusters(cards: list[EvidenceCard], drugs: list[str]) -> MechanismGraph:
    text_by_type = {
        risk_type: " ".join(card.claim_text.lower() for card in cards if any(term in card.claim_text.lower() for term in terms))
        for risk_type, terms in _TOXICITY_TERMS.items()
    }
    clusters: list[MechanismCluster] = []
    for risk_type, text in text_by_type.items():
        if not text:
            continue
        evidence_ids = [card.evidence_id for card in cards if any(term in card.claim_text.lower() for term in _TOXICITY_TERMS[risk_type])]
        clusters.append(
            MechanismCluster(
                cluster_id=f"cluster:toxicity:{risk_type.lower()}",
                label=f"{risk_type.title()} toxicity convergence",
                risk_type=risk_type,
                drivers=drugs,
                affected_drugs=drugs,
                confidence="low",
                evidence_ids=evidence_ids,
            )
        )
    return MechanismGraph(clusters=clusters)
