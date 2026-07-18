from __future__ import annotations

from typing import Any

from src.domain.evidence.entities import EvidenceCard
from src.domain.mechanism.entities import MechanismCluster, MechanismEdge, MechanismGraph, MechanismNode


def detect_pd_mechanisms(context: dict[str, Any], cards: list[EvidenceCard], pair: list[str]) -> MechanismGraph:
    pkpd = context.get("pkpd") or {}
    pd_detail = pkpd.get("pd_detail") or {}
    targets = pd_detail.get("overlap_targets") or []
    pathways = pd_detail.get("overlap_pathways") or []
    if not (targets or pathways):
        return MechanismGraph()

    evidence_ids = [card.evidence_id for card in cards if card.claim_type in {"pd_mechanism", "mechanism", "pathway_context"}]
    mechanism_id = "mechanism:pd"
    nodes = [MechanismNode(id=mechanism_id, label="PD overlap", type="mechanism", payload=pd_detail)]
    edges = [
        MechanismEdge(source=f"drug:{drug}", target=mechanism_id, type="contributes", confidence="low", evidence_ids=evidence_ids)
        for drug in pair
    ]
    clusters = [
        MechanismCluster(
            cluster_id="cluster:pd",
            label="PD overlap",
            risk_type="PD",
            drivers=pair,
            affected_drugs=pair,
            confidence="low",
            evidence_ids=evidence_ids,
        )
    ]
    return MechanismGraph(nodes=nodes, edges=edges, clusters=clusters)
