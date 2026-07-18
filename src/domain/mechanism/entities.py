from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MechanismConfidence = Literal["high", "medium", "low", "unknown"]


@dataclass(frozen=True)
class MechanismNode:
    id: str
    label: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MechanismEdge:
    source: str
    target: str
    type: str
    confidence: MechanismConfidence = "unknown"
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MechanismCluster:
    cluster_id: str
    label: str
    risk_type: str
    drivers: list[str]
    affected_drugs: list[str]
    confidence: MechanismConfidence = "unknown"
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MechanismGraph:
    nodes: list[MechanismNode] = field(default_factory=list)
    edges: list[MechanismEdge] = field(default_factory=list)
    clusters: list[MechanismCluster] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
