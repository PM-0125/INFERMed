from __future__ import annotations

from typing import Any

from src.application.interaction_modeling import build_drug_profile_graph
from src.domain.evidence.entities import EvidenceCard
from src.domain.profile.entities import DrugProfileGraph, ProfileEdge, ProfileNode

_PROFILE_SECTIONS = ("identifier", "structure", "enzyme", "transporter", "target", "pathway", "adverse_event", "toxicity_marker", "label_section")


class DrugProfileGraphBuilder:
    """Build independent profile graphs from normalized source context."""

    def build_for_drug(
        self,
        *,
        analysis_id: str,
        drug: str,
        context: dict[str, Any],
        evidence_cards: list[EvidenceCard],
    ) -> DrugProfileGraph:
        pair_graph = build_drug_profile_graph(
            analysis_id=analysis_id,
            drugs=[drug],
            context=context,
            evidence_cards=evidence_cards,
        )
        return self._ensure_explicit_gaps(pair_graph, drug)

    def merge(self, *, analysis_id: str, drugs: list[str], graphs: list[DrugProfileGraph]) -> DrugProfileGraph:
        nodes: dict[str, ProfileNode] = {}
        edges: dict[tuple[str, str, str], ProfileEdge] = {}
        missing: dict[str, set[str]] = {drug: set() for drug in drugs}
        limitations: list[str] = []
        for graph in graphs:
            for node in graph.nodes:
                nodes.setdefault(node.node_id, node)
            for edge in graph.edges:
                edges.setdefault((edge.source_id, edge.target_id, edge.type), edge)
            for drug, gaps in graph.missing_elements.items():
                missing.setdefault(drug, set()).update(gaps)
            limitations.extend(graph.source_limitations)
        return DrugProfileGraph(
            analysis_id=analysis_id,
            drugs=drugs,
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            missing_elements={drug: sorted(gaps) for drug, gaps in missing.items() if gaps},
            source_limitations=list(dict.fromkeys(limitations)),
        )

    def _ensure_explicit_gaps(self, graph: DrugProfileGraph, drug: str) -> DrugProfileGraph:
        observed = {node.type for node in graph.nodes if drug.lower() in {item.lower() for item in node.drug_scope}}
        missing = set(graph.missing_elements.get(drug, []))
        for section in _PROFILE_SECTIONS:
            if section not in observed:
                missing.add(section)
        return DrugProfileGraph(
            analysis_id=graph.analysis_id,
            drugs=graph.drugs,
            nodes=graph.nodes,
            edges=graph.edges,
            missing_elements={**graph.missing_elements, drug: sorted(missing)},
            source_limitations=graph.source_limitations,
        )
