from test_passage_grounding import label, decision
from src.domain.explanation.verifier import _check_claims
from src.domain.explanation.action_support import enforce_action_support
from src.domain.explanation.applicability import assess_applicability


def test_source_supported_advice_with_missing_patient_condition_still_requires_review():
    from types import SimpleNamespace
    from src.domain.explanation.verifier import ExplanationVerifier

    text = "Monitor alpha levels if severe renal impairment is present."
    result = ExplanationVerifier().verify(
        answer_text=text + " [ev_label]",
        decision=decision(),
        evidence_cards=[label(text)],
        safety_report=SimpleNamespace(requires_review=False, findings=[]),
        patient_context={},
    )
    assert result.claims[0]["status"] == "supported"
    assert result.status == "review_required"


def test_exact_label_support_does_not_establish_patient_applicability():
    text = "Monitor alpha levels if severe renal impairment is present."
    claim = _check_claims(
        text + " [ev_label]", decision(), [label(text)], patient_context={}
    )[0]
    assert claim["status"] == "supported"
    assert claim["patient_applicability"]["status"] == "missing_context"
    assert "renal_function" in claim["patient_applicability"]["required_fields"]


def test_known_conflicting_condition_withholds_action_and_reports_why():
    text = "Monitor alpha levels if severe renal impairment is present."
    result = enforce_action_support(
        text + " [ev_label]",
        [label(text)],
        ["alpha", "beta"],
        patient_context={"renal_function": "normal"},
    )
    assert result.supported_count == 0
    assert result.findings[0]["applicability"]["status"] == "condition_conflict"


def test_matching_condition_is_not_called_clinically_validated():
    result = assess_applicability(
        "Monitor alpha if severe renal impairment is present.",
        {"renal_function": "severe_impairment"},
        "prescribing_advice",
    )
    assert result["status"] == "conditions_matched"
    assert "not clinical validation" in result["limitation"]


def test_study_population_is_not_patient_regimen():
    result = assess_applicability(
        "Alpha 15 mg was administered to 13 healthy male volunteers.",
        {"age_years": 79, "renal_function": "severe_impairment"},
        "study_observation",
    )
    assert result["status"] == "study_population_only"
    assert "renal_function" in result["population_differences"]


def test_comparator_normal_is_not_mistaken_for_index_study_population():
    for text in [
        "The alpha study in elderly subjects reported lower clearance than normal young male volunteers.",
        "The alpha study in 22 elderly subjects reported lower clearance than that observed in 12 normal young male volunteers.",
        "Compared with responses in 12 healthy male volunteers, the study observed reduced clearance.",
    ]:
        result = assess_applicability(
            text,
            {"renal_function": "severe_impairment", "sex": "female"},
            "study_observation",
        )
        assert result["population_differences"] == []


def test_study_age_match_does_not_establish_renal_applicability():
    from test_renal_study_fields import CLAIM

    for age, state in [(79, "matched"), (40, "conflict"), (None, "unknown")]:
        result = assess_applicability(
            CLAIM,
            {"age_years": age, "renal_function": "severe_impairment"},
            "study_observation",
        )
        assert result["status"] == "study_population_only"
        assert result["checked_conditions"][0]["field"] == "age_years"
        assert result["checked_conditions"][0]["state"] == state
        assert "renal_function" not in result["population_differences"]


def test_renal_category_does_not_satisfy_numeric_clearance_threshold():
    text = "Monitor alpha if CrCl < 30 mL/min."
    result = assess_applicability(
        text, {"renal_function": "severe_impairment"}, "prescribing_advice"
    )
    assert result["status"] == "missing_context"
    assert "creatinine_clearance_ml_min" in result["required_fields"]
    wrong_metric = assess_applicability(
        text,
        {"labs": [{"name": "eGFR", "value": 25, "unit": "mL/min/1.73m2"}]},
        "prescribing_advice",
    )
    assert wrong_metric["status"] == "missing_context"
    measured = assess_applicability(
        text,
        {"labs": [{"name": "CrCl", "value": 25, "unit": "mL/min"}]},
        "prescribing_advice",
    )
    assert measured["status"] == "conditions_matched"


def test_missing_inr_target_does_not_become_therapeutic_inr():
    claim = _check_claims(
        "The current INR is therapeutic.",
        decision(),
        [],
        patient_context={"current_inr": 2.4},
    )[0]
    assert claim["status"] == "unsupported"
    assert "inr_target_range" in claim["patient_applicability"]["required_fields"]


def test_multiple_conditions_are_all_checked():
    result = assess_applicability(
        "Monitor alpha if severe renal impairment is present in pregnant patients.",
        {"renal_function": "severe_impairment", "pregnancy_status": "unknown"},
        "prescribing_advice",
    )
    assert result["status"] == "missing_context"
    assert "pregnancy_status" in result["required_fields"]


def test_negated_or_alternative_conditions_are_not_silently_reversed():
    for text in [
        "Monitor alpha if severe renal impairment is not present.",
        "Monitor alpha in patients with mild or severe renal impairment.",
    ]:
        result = assess_applicability(
            text, {"renal_function": "normal"}, "prescribing_advice"
        )
        assert result["status"] == "not_established"


def test_analysis_verification_receives_actual_patient_context(tmp_path):
    from src.application.commands import AnalyzeMedicationSetCommand
    from src.application.use_cases.analyze_medication_set import (
        AnalyzeMedicationSetUseCase,
    )
    from src.infrastructure.persistence.event_store import SQLiteEventStore

    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {},
        answer_generator=lambda *a, **k: {"text": "The current INR is therapeutic."},
        event_store=SQLiteEventStore(tmp_path / "audit.sqlite"),
    ).execute(
        AnalyzeMedicationSetCommand(
            medications=["alpha", "beta"],
            patient_context={
                "current_inr": 2.4,
                "inr_target_range": [2, 3],
                "indication": "supplied treatment indication",
            },
        )
    )
    assert (
        "inr_target_range"
        not in result.verification["claims"][0]["patient_applicability"][
            "required_fields"
        ]
    )


def test_non_pregnant_and_absent_conditions_are_not_reversed():
    for text in [
        "Monitor alpha in non-pregnant patients with severe renal impairment.",
        "Monitor alpha if severe renal impairment is absent.",
        "Monitor alpha in patients with no severe renal impairment.",
        "Monitor alpha if severe renal impairment has resolved.",
    ]:
        result = assess_applicability(
            text,
            {"renal_function": "severe_impairment", "pregnancy_status": "pregnant"},
            "prescribing_advice",
        )
        assert result["status"] == "not_established"


def test_both_numeric_bounds_must_be_checked():
    result = assess_applicability(
        "Monitor alpha if CrCl > 15 mL/min and CrCl < 30 mL/min.",
        {"labs": [{"name": "CrCl", "value": 80, "unit": "mL/min"}]},
        "prescribing_advice",
    )
    assert result["status"] == "condition_conflict"
    assert len(result["checked_conditions"]) == 2
    result = assess_applicability(
        "Monitor alpha if age over 18 and age under 65.",
        {"age_years": 80},
        "prescribing_advice",
    )
    assert result["status"] == "condition_conflict"


def test_followup_uses_updated_patient_context_for_action_gate():
    from src.application.use_cases.answer_followup import (
        AnswerFollowUpCommand,
        AnswerFollowUpUseCase,
    )

    text = "Monitor alpha levels if severe renal impairment is present."
    context = {
        "medication_set": {"patient_context": {"renal_function": "severe_impairment"}},
        "reasoning_input": {"evidence": [label(text).to_dict()]},
    }
    result = AnswerFollowUpUseCase(
        followup_generator=lambda *a, **k: {"text": text + " [ev_label]"},
        context_runner=lambda *a, **k: context,
        cited_cards_from_context=lambda ctx: [],
    ).execute(
        AnswerFollowUpCommand(
            question="Monitoring?",
            drugs=["alpha", "beta"],
            patient_context={"renal_function": "normal"},
        )
    )
    assert text not in result.answer
    assert result.publication["withheld_count"] == 1
