from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

AudienceMode = Literal["doctor", "patient", "pv_research"]
AnalysisDepth = Literal["standard", "deep_research"]


@dataclass(frozen=True)
class AnalyzeMedicationSetCommand:
    medications: list[str]
    audience: AudienceMode = "doctor"
    patient_context: dict[str, Any] | None = None
    refresh_evidence: bool = False
    analysis_depth: AnalysisDepth = "standard"
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def request_hash(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
