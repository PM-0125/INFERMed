from __future__ import annotations

from typing import Any

from src.domain.evidence.entities import EvidenceCard
from src.domain.reasoning.interpretation import biological_terms, interpret_pair
from src.domain.mechanism.entities import (
    MechanismCluster,
    MechanismEdge,
    MechanismGraph,
    MechanismNode,
)


def detect_pk_mechanisms(
    context: dict[str, Any], cards: list[EvidenceCard], pair: list[str]
) -> MechanismGraph:
    pkpd = context.get("pkpd") or {}
    pk_detail = pkpd.get("pk_detail") or {}
    evidence_ids = [
        card.evidence_id
        for card in cards
        if card.claim_type in {"mechanism", "pk_mechanism", "mechanism_reference"}
        and set(pair).issubset(card.drug_scope)
    ]
    canonical = pk_detail.get("canonical_interaction") or {}
    if (
        isinstance(canonical, dict)
        and {canonical.get("drug_a"), canonical.get("drug_b")} == set(pair)
        and canonical.get("primary_affected_drug") in pair
    ):
        affected = canonical["primary_affected_drug"]
        actor = next(drug for drug in pair if drug != affected)
        ids = [
            card.evidence_id
            for card in cards
            if card.claim_type == "mechanism"
            and card.payload == canonical
            and set(card.drug_scope) == set(pair)
        ]
        return MechanismGraph(
            nodes=[
                MechanismNode(
                    "mechanism:pk:canonical",
                    "Canonical PK interaction",
                    "mechanism",
                    {**canonical, "knowledge_basis": "retrieved_canonical_interaction"},
                )
            ],
            edges=[
                MechanismEdge(
                    f"drug:{actor}", "mechanism:pk:canonical", "drives", "medium", ids
                ),
                MechanismEdge(
                    "mechanism:pk:canonical",
                    f"drug:{affected}",
                    "affects",
                    "medium",
                    ids,
                ),
            ],
            clusters=[
                MechanismCluster(
                    "cluster:pk:canonical",
                    "Canonical PK interaction",
                    "PK",
                    [actor],
                    [affected],
                    "medium",
                    ids,
                )
            ],
        )
    chains = interpret_pair(context, pair)["causal_chains"]
    if chains:
        nodes, edges, clusters = [], [], []
        for index, chain in enumerate(chains):
            actor_ids = _role_evidence_ids(
                cards,
                chain["perpetrator"],
                chain["protein"],
                "inhibitor" if chain["action"] == "inhibition" else "inducer",
            )
            affected_ids = _role_evidence_ids(
                cards, chain["affected_drug"], chain["protein"], "substrate"
            )
            mechanism_id = f"mechanism:pk:{index}"
            nodes.append(
                MechanismNode(
                    id=mechanism_id,
                    label=f"Potential {chain['protein']} {chain['action']}",
                    type="mechanism",
                    payload=chain,
                )
            )
            edges.extend(
                [
                    MechanismEdge(
                        source=f"drug:{chain['perpetrator']}",
                        target=mechanism_id,
                        type="drives",
                        confidence="low",
                        evidence_ids=actor_ids,
                    ),
                    MechanismEdge(
                        source=mechanism_id,
                        target=f"drug:{chain['affected_drug']}",
                        type="affects",
                        confidence="low",
                        evidence_ids=affected_ids,
                    ),
                ]
            )
            clusters.append(
                MechanismCluster(
                    cluster_id=f"cluster:pk:{index}",
                    label=f"Role-derived {chain['protein']} {chain['action']} hypothesis",
                    risk_type="PK",
                    drivers=[chain["perpetrator"]],
                    affected_drugs=[chain["affected_drug"]],
                    confidence="low",
                    evidence_ids=list(dict.fromkeys(actor_ids + affected_ids)),
                )
            )
        return MechanismGraph(nodes=nodes, edges=edges, clusters=clusters)

    # A prose summary can be a negative finding or reflect incomplete retrieval.
    # Preserve explicit evidence without assigning a perpetrator by input order.
    if not (
        pk_detail.get("canonical_interaction")
        or biological_terms(pk_detail.get("overlaps"))
    ):
        return MechanismGraph()

    mechanism_id = "mechanism:pk"
    nodes = [
        MechanismNode(
            id=mechanism_id,
            label="PK evidence; direction unresolved",
            type="mechanism",
            payload={**pk_detail, "direction": "unresolved"},
        )
    ]
    edges = [
        MechanismEdge(
            source=f"drug:{drug}",
            target=mechanism_id,
            type="associated_with",
            confidence="unknown",
            evidence_ids=evidence_ids,
        )
        for drug in pair
    ]
    clusters = [
        MechanismCluster(
            cluster_id="cluster:pk",
            label="PK evidence; direction unresolved",
            risk_type="PK",
            drivers=[],
            affected_drugs=pair,
            confidence="unknown",
            evidence_ids=evidence_ids,
        )
    ]
    return MechanismGraph(nodes=nodes, edges=edges, clusters=clusters)


def _role_evidence_ids(
    cards: list[EvidenceCard], drug: str, protein: str, role: str
) -> list[str]:
    return [
        card.evidence_id
        for card in cards
        if card.claim_type == "enzyme_role"
        and card.drug_scope == [drug]
        and card.payload.get("protein") == protein
        and card.payload.get("role") == role
    ]
