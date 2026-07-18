from __future__ import annotations

import argparse
import csv
import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.commands import AnalyzeMedicationSetCommand
from src.application.use_cases.analyze_medication_set import AnalyzeMedicationSetUseCase
from src.evaluation.cases import baseline_cases
from src.evaluation.safety_checks import evaluate_answer_text
from src.llm.rag_pipeline import run_rag


DEFAULT_CASE_PATH = PROJECT_ROOT / "TESTCASES" / "infermed_5_drug_benchmark_100.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run INFERMed medication-set benchmark checks without committing private testcases.")
    parser.add_argument("--cases", default=str(DEFAULT_CASE_PATH), help="CSV testcase file. Defaults to TESTCASES/infermed_5_drug_benchmark_100.csv")
    parser.add_argument("--limit", type=int, default=10, help="Maximum cases to evaluate.")
    parser.add_argument("--live-retrieval", action="store_true", help="Use live/local retrieval sources instead of mock context.")
    parser.add_argument("--live-llm", action="store_true", help="Use the configured LLM for final answers. Implies --live-retrieval.")
    parser.add_argument("--baseline", action="store_true", help="Run built-in deterministic baseline cases instead of private CSV.")
    parser.add_argument("--audience", default="doctor", choices=["doctor", "patient", "pv_research"])
    args = parser.parse_args()

    if args.baseline:
        cases = [case.to_dict() for case in baseline_cases()][: max(args.limit, 0)]
    else:
        case_path = Path(args.cases)
        if not case_path.exists():
            print(json.dumps({"ok": False, "error": f"Case file not found: {case_path}"}, indent=2))
            return 2
        cases = _load_cases(case_path)[: max(args.limit, 0)]
    if not cases:
        print(json.dumps({"ok": False, "error": "No cases loaded."}, indent=2))
        return 2

    use_live_retrieval = bool(args.live_retrieval or args.live_llm)
    context_runner = _live_context_runner if use_live_retrieval else _mock_context_runner
    answer_generator = _live_answer_generator if args.live_llm else _mock_answer_generator

    results = []
    failures = []
    for case in cases:
        drugs = case["drugs"]
        expected_pair_count = len(list(combinations(drugs, 2)))
        try:
            analysis = AnalyzeMedicationSetUseCase(
                rag_runner=run_rag,
                context_runner=context_runner,
                answer_generator=answer_generator,
            ).execute(
                AnalyzeMedicationSetCommand(
                    medications=drugs,
                    audience=args.audience,  # type: ignore[arg-type]
                    analysis_depth="standard",
                    refresh_evidence=False,
                )
            )
            checks = {
                "pair_count_ok": analysis.evidence_plan.pair_count == expected_pair_count,
                "executed_all_pairs": len(analysis.executed_pairs) == expected_pair_count,
                "medication_set_scope": analysis.decision.interaction_scope == ("medication_set" if len(drugs) > 2 else "pair"),
                "has_top_pairs": bool((analysis.aggregate_context.get("medication_set") or {}).get("top_pairs")),
                "no_high_severity_safety_findings": not any(
                    finding.severity in {"high", "critical"} for finding in analysis.safety_report.findings
                ),
            }
            quality = evaluate_answer_text(
                str((analysis.rag_output.get("answer") or {}).get("text") or ""),
                unknown_or_research=case.get("case_type") == "unknown_research",
            )
            checks.update({f"answer_{key}": value for key, value in quality.checks.items()})
            row = {
                "case_id": case["case_id"],
                "drugs": drugs,
                "expected_pair_count": expected_pair_count,
                "executed_pair_count": len(analysis.executed_pairs),
                "risk_level": analysis.decision.risk_level,
                "confidence": analysis.decision.confidence,
                "checks": checks,
                "quality_failures": quality.failures,
            }
            if not all(checks.values()):
                failures.append(row)
            results.append(row)
        except Exception as exc:
            row = {
                "case_id": case["case_id"],
                "drugs": drugs,
                "expected_pair_count": expected_pair_count,
                "error": str(exc),
            }
            failures.append(row)
            results.append(row)

    summary = {
        "ok": not failures,
        "mode": "live_llm" if args.live_llm else "live_retrieval" if use_live_retrieval else "mock_context",
        "case_count": len(results),
        "failure_count": len(failures),
        "results": results,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if failures else 0


def _load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            drugs = [
                str(raw.get(f"Drug_{idx}") or "").strip()
                for idx in range(1, 11)
            ]
            drugs = [drug for drug in drugs if drug]
            if len(drugs) < 2:
                continue
            rows.append(
                {
                    "case_id": raw.get("Case_ID") or f"case_{len(rows) + 1}",
                    "drugs": drugs,
                    "expected_risk_clusters": raw.get("Expected_Risk_Clusters") or "",
                    "expected_severity_band": raw.get("Expected_Severity_Band") or "",
                    "expected_behavior": raw.get("Expected_System_Behavior") or "",
                }
            )
    return rows


def _mock_context_runner(drug_a: str, drug_b: str, **kwargs: Any) -> dict[str, Any]:
    shared_signal = _shared_signal(drug_a, drug_b)
    return {
        "drugs": {"a": {"name": drug_a}, "b": {"name": drug_b}},
        "signals": {
            "tabular": {
                "prr": shared_signal["prr"],
                "side_effects_a": shared_signal["events_a"],
                "side_effects_b": shared_signal["events_b"],
                "dili_a": shared_signal["dili_a"],
                "dili_b": shared_signal["dili_b"],
                "dict_a": "unknown",
                "dict_b": "unknown",
                "diqt_a": shared_signal["diqt_a"],
                "diqt_b": shared_signal["diqt_b"],
            },
            "faers": {
                "top_reactions_a": [[event, 10] for event in shared_signal["events_a"]],
                "top_reactions_b": [[event, 8] for event in shared_signal["events_b"]],
                "combo_reactions": [[event, 3] for event in shared_signal["combo_events"]],
            },
            "mechanistic": {
                "enzymes_a": shared_signal["enzymes_a"],
                "enzymes_b": shared_signal["enzymes_b"],
                "targets_a": shared_signal["targets_a"],
                "targets_b": shared_signal["targets_b"],
            },
        },
        "pkpd": {
            "pk_summary": shared_signal["pk_summary"],
            "pd_summary": shared_signal["pd_summary"],
            "pk_detail": {"overlaps": {"inhibition": shared_signal["shared_enzymes"]}},
            "pd_detail": {"overlap_targets": shared_signal["shared_targets"], "overlap_pathways": []},
        },
        "sources": {"duckdb": ["Benchmark mock context"], "openfda": [], "apis": []},
        "source_status": [{"name": "Benchmark mock context", "enabled": True, "available": True, "reason": "offline evaluation"}],
        "caveats": ["Benchmark mock context is structural only; it does not assess clinical truth."],
    }


def _mock_answer_generator(context: dict[str, Any], mode: str, **kwargs: Any) -> dict[str, Any]:
    medset = context.get("medication_set") or {}
    pair_count = medset.get("pair_count", 1)
    return {
        "text": (
            "## Bottom Line\n"
            f"Structural benchmark response for {pair_count} evaluated pair combinations. "
            "This run checks orchestration and safety constraints, not final clinical answer quality.\n\n"
            "## Interaction Mechanism\n"
            "Mechanisms are summarized from pair-level mock evidence and remain hypothesis-level in this harness.\n\n"
            "## Clinical Concern\n"
            "Prioritize convergent toxicity and PK/PD overlap signals for human review.\n\n"
            "## Monitoring & Actions\n"
            "No patient-specific dose guidance is generated by this benchmark mode.\n\n"
            "## Evidence Limitations\n"
            "Benchmark mock context is not clinical evidence."
        ),
        "meta": {"provider": "benchmark_mock", "mode": mode},
    }


def _live_context_runner(drug_a: str, drug_b: str, **kwargs: Any) -> dict[str, Any]:
    from src.api.app import _retrieve_pair_context

    return _retrieve_pair_context(drug_a, drug_b, **kwargs)


def _live_answer_generator(context: dict[str, Any], mode: str, **kwargs: Any) -> dict[str, Any]:
    from src.api.app import _generate_final_answer

    return _generate_final_answer(context, mode, **kwargs)


def _shared_signal(drug_a: str, drug_b: str) -> dict[str, Any]:
    key = f"{drug_a} {drug_b}".lower()
    bleeding_terms = {"warfarin", "aspirin", "ibuprofen", "apixaban", "sertraline"}
    qt_terms = {"amiodarone", "fluconazole", "azithromycin", "citalopram"}
    cyp_terms = {"warfarin", "fluconazole", "amiodarone", "ketoconazole", "clarithromycin"}
    has_bleeding = any(term in key for term in bleeding_terms)
    has_qt = any(term in key for term in qt_terms)
    has_cyp = any(term in key for term in cyp_terms)
    events = []
    if has_bleeding:
        events.append("bleeding")
    if has_qt:
        events.append("qt prolongation")
    if not events:
        events.append("nausea")
    enzymes = ["CYP3A4"] if has_cyp else []
    shared_targets = ["hemostasis"] if has_bleeding else []
    return {
        "prr": 2.4 if has_bleeding or has_qt else None,
        "events_a": events,
        "events_b": events,
        "combo_events": events,
        "dili_a": 0.6 if has_cyp else "unknown",
        "dili_b": 0.6 if has_cyp else "unknown",
        "diqt_a": 0.7 if has_qt else None,
        "diqt_b": 0.7 if has_qt else None,
        "enzymes_a": enzymes,
        "enzymes_b": enzymes,
        "shared_enzymes": enzymes,
        "targets_a": shared_targets,
        "targets_b": shared_targets,
        "shared_targets": shared_targets,
        "pk_summary": "Mock shared CYP pathway signal." if enzymes else "No mock PK overlap.",
        "pd_summary": "Mock shared bleeding/QT toxicity signal." if events else "No mock PD overlap.",
    }


if __name__ == "__main__":
    raise SystemExit(main())
