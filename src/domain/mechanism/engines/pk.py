from __future__ import annotations

from typing import Any

from src.domain.evidence.entities import EvidenceCard
from src.domain.mechanism.entities import MechanismCluster, MechanismEdge, MechanismGraph, MechanismNode


def detect_pk_mechanisms(context: dict[str, Any], cards: list[EvidenceCard], pair: list[str]) -> MechanismGraph:
    pkpd = context.get("pkpd") or {}
    pk_detail = pkpd.get("pk_detail") or {}
    evidence_ids = [card.evidence_id for card in cards if card.claim_type in {"mechanism", "pk_mechanism", "mechanism_reference"}]
    if not (pk_detail.get("canonical_interaction") or pkpd.get("pk_summary") or any((pk_detail.get("overlaps") or {}).values())):
        return MechanismGraph()

    mechanism_id = "mechanism:pk"
    nodes = [MechanismNode(id=mechanism_id, label="PK mechanism", type="mechanism", payload=pk_detail)]
    edges = [
        MechanismEdge(source=f"drug:{pair[1]}", target=mechanism_id, type="drives", confidence="medium", evidence_ids=evidence_ids),
        MechanismEdge(source=mechanism_id, target=f"drug:{pair[0]}", type="affects", confidence="medium", evidence_ids=evidence_ids),
    ]
    clusters = [
        MechanismCluster(
            cluster_id="cluster:pk",
            label="PK interaction mechanism",
            risk_type="PK",
            drivers=[pair[1]],
            affected_drugs=[pair[0]],
            confidence="medium",
            evidence_ids=evidence_ids,
        )
    ]
    return MechanismGraph(nodes=nodes, edges=edges, clusters=clusters)
