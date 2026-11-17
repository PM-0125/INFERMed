from copy import deepcopy

import pytest

from src.application.commands import AnalyzeMedicationSetCommand
from src.application.use_cases.analyze_medication_set import AnalyzeMedicationSetUseCase
from src.infrastructure.persistence.event_store import SQLiteEventStore


@pytest.mark.parametrize("drugs", [["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]])
def test_reasoning_exists_before_generation_and_snapshot_is_isolated(tmp_path, drugs):
    captured = []

    def context(a, b, **kwargs):
        return {
            "drugs": {"a": {"name": a}, "b": {"name": b}},
            "pkpd": {"pk_detail": {"overlaps": {"inhibition": ["CYP3A4"]}}},
        }

    def generate(ctx, *args, **kwargs):
        assert ctx["reasoning_input"]["decision"]
        interpretations = ctx["reasoning_input"]["pair_interpretations"]
        assert len(interpretations) == len(drugs) * (len(drugs) - 1) // 2
        assert all(len(row["drug_scope"]) == 2 for row in interpretations)
        assert ctx["reasoning_input"]["reasoning"]["hypotheses"]
        captured.append(deepcopy(ctx))
        ctx["reasoning_input"].clear()
        return {
            "text": "Evidence is limited; shared proteins suggest a hypothesis, not proof."
        }

    store = SQLiteEventStore(tmp_path / "events.sqlite")
    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=context,
        answer_generator=generate,
        event_store=store,
    ).execute(AnalyzeMedicationSetCommand(medications=drugs))
    assert len(captured) == 1
    assert result.aggregate_context["reasoning_input"] == captured[0]["reasoning_input"]
    assert (
        store.get_analysis_snapshot(result.analysis_id)["context"]["reasoning_input"]
        == captured[0]["reasoning_input"]
    )


def test_both_prompt_builders_include_reasoning_contract():
    from src.llm.llm_interface import build_prompt, build_compact_analysis_prompt

    ctx = {
        "drugs": {"a": {"name": "a"}, "b": {"name": "b"}},
        "reasoning_input": {
            "schema_version": 1,
            "evidence": [{"evidence_id": "ev_graph_sentinel"}],
            "uncertainty": ["missing-sentinel"],
        },
    }
    for builder in (build_prompt, build_compact_analysis_prompt):
        prompt = builder(ctx, "doctor")
        assert "ev_graph_sentinel" in prompt
        assert "missing-sentinel" in prompt


def test_provider_failure_is_not_a_successful_assessment(tmp_path):
    use_case = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {},
        answer_generator=lambda *a, **k: {
            "text": "Unable to generate a full explanation right now. Ollama error: disconnected"
        },
        event_store=SQLiteEventStore(tmp_path / "events.sqlite"),
    )
    with pytest.raises(RuntimeError, match="generation"):
        use_case.execute(AnalyzeMedicationSetCommand(medications=["a", "b"]))


def test_revision_audit_is_persisted_and_not_clinically_approved(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite")
    review = {
        "status": "reviewed_not_validated",
        "attempts": 1,
        "draft": {"text": "Original model draft"},
        "findings": [],
        "remaining_findings": [],
    }
    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {},
        answer_generator=lambda *a, **k: {
            "text": "Revised model answer",
            "review": review,
        },
        event_store=store,
    ).execute(AnalyzeMedicationSetCommand(medications=["a", "b"]))
    assert result.verification["status"] == "review_required"
    assert result.verification["audience_review"]["status"] == "reviewed_not_validated"
    saved = store.get_analysis_snapshot(result.analysis_id)["response_read_model"][
        "answer"
    ]
    assert saved["review"]["draft"]["text"] == "Original model draft"


def test_followup_patient_update_does_not_reuse_stale_amplifiers():
    from src.application.use_cases.answer_followup import _context_with_patient_context

    ctx = {
        "reasoning_input": {
            "decision": {"patient_amplifiers": ["advanced age"]},
            "medication_set": {},
        },
        "medication_set": {},
    }
    updated = _context_with_patient_context(
        ctx, {"age_years": 40, "renal_function": "severe_impairment"}
    )
    assert updated["reasoning_input"]["decision"]["patient_amplifiers"] == [
        "renal impairment"
    ]
    assert ctx["reasoning_input"]["decision"]["patient_amplifiers"] == ["advanced age"]


def test_graph_overlap_is_not_contradicted_by_legacy_empty_summary():
    from src.llm.llm_interface import _summarize_context

    context = {
        "reasoning_input": {
            "reasoning": {
                "signals": [
                    {
                        "category": "pk_overlap",
                        "label": "Shared PK-relevant proteins: CYP2C9",
                        "drug_scope": ["a", "b"],
                    }
                ]
            }
        }
    }
    summary = _summarize_context(context, "doctor")
    assert "CYP2C9" in summary["PK_SUMMARY"]
    assert "No strong PK overlap" not in summary["PK_SUMMARY"]
