from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from src.domain.evidence.entities import EvidenceCard


@dataclass(frozen=True)
class SourceToolInput:
    tool_name: str
    source_name: str
    query_type: str
    normalized_concept_ids: list[str]
    parameters: dict[str, Any] = field(default_factory=dict)
    source_version: str = "1"

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "source_name": self.source_name,
            "query_type": self.query_type,
            "normalized_concept_ids": sorted(self.normalized_concept_ids),
            "parameters": self.parameters,
            "source_version": self.source_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceToolResult:
    tool_name: str
    source_name: str
    ok: bool
    evidence_cards: list[EvidenceCard] = field(default_factory=list)
    normalized_payload: dict[str, Any] = field(default_factory=dict)
    raw_payload_hash: str | None = None
    error: str | None = None
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence_cards": [card.to_dict() for card in self.evidence_cards],
        }


class SourceTool(Protocol):
    name: str
    source_name: str
    version: str

    def execute(self, tool_input: SourceToolInput) -> SourceToolResult:
        ...
