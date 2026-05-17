from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceStatus:
    name: str
    enabled: bool
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class EvidenceItem:
    source: str
    evidence_type: str
    subject: str
    predicate: str
    object: Any
    confidence: str = "unknown"
    visibility: str = "public"
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceBundle:
    drug_a: str
    drug_b: str
    items: list[EvidenceItem]
    source_status: list[SourceStatus]
    caveats: list[str]
