from __future__ import annotations

from src.domain.evidence.entities import EvidenceCard
from src.domain.mechanism.engines.toxicity import detect_toxicity_clusters
from src.domain.mechanism.entities import MechanismCluster, MechanismEdge, MechanismGraph, MechanismNode


def build_ndrug_mechanism_graph(
    *,
    drugs: list[str],
    pair_graphs: list[MechanismGraph],
    evidence_cards: list[EvidenceCard],
) -> MechanismGraph:
    nodes: dict[str, MechanismNode] = {}
    edges: dict[tuple[str, str, str], MechanismEdge] = {}
    clusters: dict[str, MechanismCluster] = {}

    for drug in drugs:
        nodes[f"drug:{drug}"] = MechanismNode(id=f"drug:{drug}", label=drug, type="drug")

    for graph in pair_graphs:
        for node in graph.nodes:
            nodes.setdefault(node.id, node)
        for edge in graph.edges:
            edges.setdefault((edge.source, edge.target, edge.type), edge)
        for cluster in graph.clusters:
            clusters.setdefault(cluster.cluster_id, cluster)

    toxicity = detect_toxicity_clusters(evidence_cards, drugs)
    for cluster in toxicity.clusters:
        clusters.setdefault(cluster.cluster_id, cluster)

    if len(drugs) > 2:
        summary_ids = [card.evidence_id for card in evidence_cards if card.claim_type == "medication_set_summary"]
        clusters.setdefault(
            "cluster:medication_set",
            MechanismCluster(
                cluster_id="cluster:medication_set",
                label="Medication-set cluster review",
                risk_type="N_DRUG",
                drivers=drugs,
                affected_drugs=drugs,
                confidence="medium",
                evidence_ids=summary_ids,
            ),
        )

    return MechanismGraph(nodes=list(nodes.values()), edges=list(edges.values()), clusters=list(clusters.values()))
