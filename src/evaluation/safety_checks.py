from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_NUMERIC_DOSE_RE = re.compile(r"\b(?:reduce|increase|adjust)\b.{0,40}\b\d{1,3}\s*(?:-|to)?\s*\d{0,3}\s*%", re.I)
_FAERS_CAUSAL_RE = re.compile(r"\b(?:faers|prr|spontaneous report|offsides|twosides)\b.{0,90}\b(?:proves?|confirms?|establishes?|demonstrates?)\b", re.I | re.S)
_FULL_ANSWER_HEADINGS = ("## Bottom Line", "## Interaction Mechanism", "## Clinical Concern", "## Monitoring & Actions", "## Evidence Limitations")


@dataclass(frozen=True)
class AnswerQualityCheck:
    passed: bool
    failures: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_answer_text(answer_text: str, *, followup: bool = False, unknown_or_research: bool = False) -> AnswerQualityCheck:
    text = answer_text or ""
    lower = text.lower()
    checks = {
        "no_numeric_dose_guidance": not _NUMERIC_DOSE_RE.search(text),
        "no_faers_causal_overclaim": not _FAERS_CAUSAL_RE.search(text),
        "has_association_caveat_when_faers_present": ("faers" not in lower and "prr" not in lower) or any(term in lower for term in ("associative", "not causal", "not prove", "signal")),
        "unknowns_are_scoped": not unknown_or_research or any(term in lower for term in ("hypothesis", "uncertain", "limited", "not established", "insufficient")),
        "followup_is_not_full_repeat": not followup or not any(heading.lower() in lower for heading in (h.lower() for h in _FULL_ANSWER_HEADINGS)),
    }
    failures = [name for name, ok in checks.items() if not ok]
    return AnswerQualityCheck(passed=not failures, failures=failures, checks=checks)
