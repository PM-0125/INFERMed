from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Callable

from src.evaluation.metrics import EvaluationScore, pass_fail


def run_case_checks(
    cases: Iterable[dict[str, Any]],
    evaluator: Callable[[dict[str, Any]], dict[str, bool]],
) -> list[EvaluationScore]:
    scores: list[EvaluationScore] = []
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"case_{index}")
        checks = evaluator(case)
        scores.append(pass_fail(case_id, checks, payload={"case": case}))
    return scores
