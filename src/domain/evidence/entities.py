from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EvidenceGrade = Literal[
    "label",
    "guideline",
    "clinical_study",
    "pk_study",
    "mechanistic",
    "pharmacovigilance_signal",
    "inferred_hypothesis",
    "insufficient",
]


@dataclass(frozen=True)
class EvidenceCard:
    evidence_id: str
    analysis_id: str
    source_name: str
    source_type: str
    claim_type: str
    claim_text: str
    evidence_grade: EvidenceGrade
    drug_scope: list[str]
    provenance: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceSource:
    name: str
    source_type: str
    version: str | None = None
    url: str | None = None
    retrieved_at: str | None = None
    payload_hash: str | None = None
    license_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    claim_type: str
    text: str
    grade: EvidenceGrade
    source: EvidenceSource
    drug_scope: list[str]
    qualifiers: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceCaveat:
    source_name: str
    caveat_type: str
    text: str
    severity: Literal["info", "low", "medium", "high"] = "info"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_id_for(analysis_id: str, source_name: str, claim_type: str, claim_text: str) -> str:
    payload = {
        "analysis_id": analysis_id,
        "source_name": source_name,
        "claim_type": claim_type,
        "claim_text": claim_text,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "ev_" + hashlib.sha256(encoded).hexdigest()[:16]
