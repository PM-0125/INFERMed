from __future__ import annotations

from typing import Any

from src.domain.decision.entities import RiskLevel
from src.domain.reasoning.interpretation import biological_terms


def score_pair_context(context: dict[str, Any]) -> tuple[RiskLevel, str, int]:
    signals = context.get("signals") or {}
    tabular = signals.get("tabular") or {}
    faers = signals.get("faers") or {}
    pkpd = context.get("pkpd") or {}
    pk_detail = pkpd.get("pk_detail") or {}
    canonical = pk_detail.get("canonical_interaction") or {}
    if isinstance(canonical, dict):
        severity = str(canonical.get("severity") or "").lower()
        levels = {
            "high": "major",
            "severe": "major",
            "major": "major",
            "contraindicated": "contraindicated",
            "avoid": "avoid",
            "moderate": "moderate",
            "minor": "minor",
            "low": "minor",
        }
        if severity in levels:
            return levels[severity], "high", risk_rank(levels[severity])

    score = 0
    if pk_detail.get("canonical_interaction"):
        score += 3
    if biological_terms(pk_detail.get("overlaps")):
        score += 2
    if faers.get("combo_reactions"):
        score += 1
    try:
        prr = float(tabular.get("prr"))
        if prr > 2:
            score += 2
        elif prr > 1.5:
            score += 1
    except Exception:
        pass

    # Associative signals and shared proteins cannot establish clinical severity.
    if not pk_detail.get("canonical_interaction"):
        if score:
            return "theoretical", "low", score
        return "insufficient_evidence", "low", score
    # The source may establish a mechanism without specifying clinical severity.
    # Signal counts cannot supply that missing severity, even alongside a rule.
    return "insufficient_evidence", "low", score


def risk_rank(risk_level: str) -> int:
    order = {
        "insufficient_evidence": 0,
        "no_clinically_significant_interaction_found": 1,
        "theoretical": 2,
        "minor": 3,
        "moderate": 4,
        "major": 5,
        "avoid": 6,
        "contraindicated": 7,
    }
    return order.get(str(risk_level), 0)
