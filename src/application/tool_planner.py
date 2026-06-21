from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from src.domain.medication.entities import MedicationConcept
from src.infrastructure.tools.registry import ToolDefinition, ToolRegistry, default_tool_registry


@dataclass(frozen=True)
class EvidencePlan:
    drug_count: int
    pair_count: int
    priority_pairs: list[tuple[str, str]]
    tools: list[ToolDefinition]
    skipped_tools: list[ToolDefinition]

    def to_dict(self) -> dict[str, object]:
        return {
            "drug_count": self.drug_count,
            "pair_count": self.pair_count,
            "priority_pairs": [list(pair) for pair in self.priority_pairs],
            "tools": [tool.to_dict() for tool in self.tools],
            "skipped_tools": [tool.to_dict() for tool in self.skipped_tools],
        }


class ToolPlanner:
    def __init__(self, registry: ToolRegistry | None = None):
        self.registry = registry or default_tool_registry()

    def create_plan(self, concepts: list[MedicationConcept], *, data_mode: str) -> EvidencePlan:
        names = [concept.normalized_name for concept in concepts if concept.normalized_name]
        pairs = list(combinations(names, 2))
        return EvidencePlan(
            drug_count=len(names),
            pair_count=len(pairs),
            priority_pairs=pairs,
            tools=self.registry.allowed_tools(data_mode),
            skipped_tools=self.registry.skipped_tools(data_mode),
        )
