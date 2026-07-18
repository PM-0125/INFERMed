from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ResearchSignalType = Literal["literature", "protein_network", "experimental_combo", "side_effect", "target_disease", "evidence_gap"]


@dataclass(frozen=True)
class ResearchSignal:
    signal_type: ResearchSignalType
    label: str
    support: Literal["context", "hypothesis", "insufficient"] = "context"
    source_names: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
