from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvaluationScore:
    case_id: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def pass_fail(case_id: str, checks: dict[str, bool], *, notes: list[str] | None = None, payload: dict[str, Any] | None = None) -> EvaluationScore:
    return EvaluationScore(case_id=case_id, passed=all(checks.values()), checks=checks, notes=notes or [], payload=payload or {})
