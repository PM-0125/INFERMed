"""One bounded, policy-filtered pass over actionable graph gaps."""

from dataclasses import dataclass, field, replace
from time import monotonic

from src.application.events import stable_hash
from src.application.source_tools import SourceToolResult


@dataclass(frozen=True)
class EnrichmentBudget:
    max_calls: int = 4
    max_rounds: int = 1
    deadline_s: float = 30
    source_timeout_s: float = 10


@dataclass
class EnrichmentResult:
    results: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    exhausted: bool = False


def enrich_profile_gaps(
    *,
    requests,
    available_tools,
    budget,
    clock=monotonic,
    attempted_inputs=(),
    progress=None,
):
    outcome = EnrichmentResult()
    deadline = clock() + max(0, budget.deadline_s)
    seen = set(attempted_inputs)
    calls = 0
    for request in requests:
        key = stable_hash(request.canonical_payload())
        reason = None
        if key in seen:
            reason = "already_attempted"
        elif request.tool_name not in available_tools:
            reason = "unavailable_or_disallowed"
        elif budget.max_rounds < 1 or calls >= budget.max_calls or clock() >= deadline:
            reason = "budget_exhausted"
            outcome.exhausted = True
        if reason:
            outcome.skipped.append({"tool": request.tool_name, "reason": reason})
            continue
        seen.add(key)
        calls += 1
        timeout = min(budget.source_timeout_s, deadline - clock())
        if timeout <= 0:
            outcome.exhausted = True
            break
        # The concrete adapter must honor this whole-request deadline.
        bounded = replace(
            request, parameters={**request.parameters, "timeout_s": timeout}
        )
        if progress:
            progress(
                "evidence_enrichment_started",
                f"Checking missing evidence via {request.source_name}.",
                {},
            )
        try:
            result = available_tools[request.tool_name].execute(bounded)
            if not isinstance(result, SourceToolResult):
                raise ValueError("Malformed tool result")
        except Exception as exc:
            result = SourceToolResult(
                request.tool_name, request.source_name, False, error=type(exc).__name__
            )
        outcome.results.append(result)
        if progress:
            progress(
                (
                    "evidence_enrichment_completed"
                    if result.ok
                    else "evidence_enrichment_failed"
                ),
                f"{request.source_name}: {'completed' if result.ok else 'unavailable'}.",
                {"ok": result.ok},
            )
    return outcome
