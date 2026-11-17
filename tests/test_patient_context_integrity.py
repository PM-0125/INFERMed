from copy import deepcopy

import pytest

from src.application.commands import AnalyzeMedicationSetCommand
from src.application.use_cases.analyze_medication_set import AnalyzeMedicationSetUseCase
from src.application.use_cases.answer_followup import _context_with_patient_context
from src.config.settings import Settings
from src.domain.decision.rules import patient_amplifiers
from src.domain.patient.services import normalize_patient_context
from src.infrastructure.persistence.event_store import SQLiteEventStore


@pytest.mark.parametrize("drugs", [["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]])
def test_supplied_patient_data_reaches_decision_generation_and_snapshot(
    tmp_path, drugs
):
    inputs = []

    def generate(context, *args, **kwargs):
        assert (
            kwargs["patient_context"] == context["reasoning_input"]["patient_context"]
        )
        inputs.append(deepcopy(context["reasoning_input"]))
        return {"text": "Patient factors require evidence-specific interpretation."}

    store = SQLiteEventStore(tmp_path / "events.sqlite")
    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *args, **kwargs: {},
        context_runner=lambda *args, **kwargs: {},
        answer_generator=generate,
        event_store=store,
        settings=Settings(),
    ).execute(
        AnalyzeMedicationSetCommand(
            medications=drugs,
            patient_context={
                "age": 79,
                "renal_function": "severe_impairment",
                "current_inr": 2.4,
            },
        )
    )
    decision = result.decision.to_dict()
    assert decision["patient_amplifiers"] == ["advanced age", "renal impairment"]
    assert "renal function" not in decision["missing_patient_factors"]
    assert "current INR" not in decision["missing_patient_factors"]
    assert "hepatic function" in decision["missing_patient_factors"]
    assert decision["risk_level"] == "insufficient_evidence"
    assert inputs[0]["patient_context"]["age_years"] == 79
    assert inputs[0]["decision"] == decision
    for pair in result.pair_results:
        assert pair.decision.patient_amplifiers == ["advanced age", "renal impairment"]
        assert "current INR" not in pair.decision.missing_patient_factors
    saved = store.get_analysis_snapshot(result.analysis_id)
    assert saved["context"]["reasoning_input"]["decision"] == decision


def test_followup_ui_alias_replaces_normalized_age_without_mutating_snapshot():
    patient = normalize_patient_context(
        {"age": 79, "renal_function": "severe_impairment"}
    ).to_dict()
    original = {
        "medication_set": {"patient_context": patient},
        "reasoning_input": {
            "patient_context": patient,
            "decision": {"risk_level": "major"},
        },
    }
    updated = _context_with_patient_context(
        original, {"age": 40, "renal_function": "normal"}
    )
    assert updated["reasoning_input"]["patient_context"]["age_years"] == 40
    assert updated["medication_set"]["patient_context"]["age_years"] == 40
    assert updated["reasoning_input"]["decision"]["patient_amplifiers"] == []
    assert updated["reasoning_input"]["decision"]["risk_level"] == "major"
    assert original["reasoning_input"]["patient_context"]["age_years"] == 79


def test_explicit_unknown_update_does_not_restore_old_patient_alias():
    original = {
        "medication_set": {"patient_context": {"age": 79, "inr": 2.4}},
        "reasoning_input": {"decision": {}},
    }
    updated = _context_with_patient_context(
        original, {"age_years": None, "current_inr": None}
    )
    patient = updated["reasoning_input"]["patient_context"]
    assert patient["age_years"] is None
    assert patient["current_inr"] is None
    assert (
        "current INR"
        in updated["reasoning_input"]["decision"]["missing_patient_factors"]
    )


def test_zero_age_is_preserved_in_normalized_patient_data():
    assert normalize_patient_context({"age_years": 0}).age_years == 0


def test_followup_retains_additional_patient_facts_from_snapshot_raw():
    patient = normalize_patient_context(
        {"age": 79, "bleeding_history": "Previous gastrointestinal bleeding"}
    ).to_dict()
    original = {
        "medication_set": {"patient_context": patient},
        "reasoning_input": {"decision": {}},
    }
    updated = _context_with_patient_context(original, {"age": 40})
    result = updated["reasoning_input"]["patient_context"]
    assert result["age_years"] == 40
    assert result["raw"]["bleeding_history"] == "Previous gastrointestinal bleeding"


def test_inr_value_alone_is_not_a_risk_amplifier():
    assert patient_amplifiers(normalize_patient_context({"current_inr": 2.4})) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"pregnancy_status": "pregnant"},
        {"labs": [{"name": "example", "value": 1}]},
        {"monitoring": [{"parameter": "example"}]},
        {"concurrent_medications": ["other medicine"]},
    ],
)
def test_patient_information_is_recognized_without_demographic_fields(payload):
    assert normalize_patient_context(payload).has_patient_specific_data
