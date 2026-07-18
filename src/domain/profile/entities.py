from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ProfileNodeType = Literal[
    "drug",
    "identifier",
    "structure",
    "enzyme",
    "transporter",
    "target",
    "pathway",
    "adverse_event",
    "toxicity_marker",
    "label_section",
    "evidence_gap",
    "source",
]

ProfileEdgeType = Literal[
    "has_identifier",
    "has_structure",
    "metabolized_by",
    "inhibits_or_modulates",
    "transported_by",
    "has_target",
    "participates_in_pathway",
    "associated_with_adverse_event",
    "has_toxicity_signal",
    "has_label_context",
    "supported_by",
    "missing",
]

EvidenceStrength = Literal["direct", "indirect", "inferred", "missing"]


@dataclass(frozen=True)
class ProfileNode:
    node_id: str
    type: ProfileNodeType
    label: str
    drug_scope: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProfileEdge:
    source_id: str
    target_id: str
    type: ProfileEdgeType
    strength: EvidenceStrength = "indirect"
    evidence_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DrugProfileGraph:
    analysis_id: str
    drugs: list[str]
    nodes: list[ProfileNode] = field(default_factory=list)
    edges: list[ProfileEdge] = field(default_factory=list)
    missing_elements: dict[str, list[str]] = field(default_factory=dict)
    source_limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def nodes_for_drug(self, drug: str, node_type: ProfileNodeType | None = None) -> list[ProfileNode]:
        drug_key = drug.lower()
        rows = [node for node in self.nodes if drug_key in {item.lower() for item in node.drug_scope}]
        if node_type is not None:
            rows = [node for node in rows if node.type == node_type]
        return rows

    def shared_nodes(self, node_type: ProfileNodeType) -> list[ProfileNode]:
        return [node for node in self.nodes if node.type == node_type and len(set(node.drug_scope)) > 1]

