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


def evidence_id_for(analysis_id: str, source_name: str, claim_type: str, claim_text: str) -> str:
    payload = {
        "analysis_id": analysis_id,
        "source_name": source_name,
        "claim_type": claim_type,
        "claim_text": claim_text,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "ev_" + hashlib.sha256(encoded).hexdigest()[:16]

