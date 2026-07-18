from __future__ import annotations

from typing import Any

from src.domain.profile.entities import DrugProfileGraph
from src.domain.research.entities import ResearchSignal


class ResearchHypothesisBuilder:
    def build(self, *, context: dict[str, Any], profile_graph: DrugProfileGraph) -> list[ResearchSignal]:
        signals: list[ResearchSignal] = []
        enrichment = ((context.get("signals") or {}).get("research_enrichment") or {})
        if enrichment.get("europe_pmc"):
            signals.append(
                ResearchSignal(
                    signal_type="literature",
                    label="Literature metadata exists for review",
                    support="context",
                    source_names=["Europe PMC"],
                    payload=enrichment.get("europe_pmc") or {},
                    limitations=["Literature metadata is discovery context; article-level claims require review."],
                )
            )
        if enrichment.get("string"):
            signals.append(
                ResearchSignal(
                    signal_type="protein_network",
                    label="Protein network context may support mechanistic hypotheses",
                    support="hypothesis",
                    source_names=["STRING"],
                    payload=enrichment.get("string") or {},
                    limitations=["STRING network evidence is not clinical DDI proof."],
                )
            )
        if enrichment.get("nci_almanac"):
            signals.append(
                ResearchSignal(
                    signal_type="experimental_combo",
                    label="NCI ALMANAC combination-response context is available",
                    support="hypothesis",
                    source_names=["NCI ALMANAC"],
                    payload=enrichment.get("nci_almanac") or {},
                    limitations=["Cancer cell-line combination response is research context, not general clinical safety proof."],
                )
            )
        if profile_graph.missing_elements:
            signals.append(
                ResearchSignal(
                    signal_type="evidence_gap",
                    label="Drug profile has missing mechanism sections",
                    support="insufficient",
                    source_names=[],
                    payload={"missing_elements": profile_graph.missing_elements},
                    limitations=["Missing profile sections must be treated as uncertainty, not negative evidence."],
                )
            )
        return signals
