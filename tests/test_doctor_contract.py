import json
from dataclasses import replace
import pytest

from test_passage_grounding import label
from src.application import doctor_contract as dc


def context(*cards, patient=None):
    return {
        "reasoning_input": {
            "decision": {"drugs": ["alpha", "beta"]},
            "evidence": [c.to_dict() for c in cards],
            "patient_context": patient or {},
        }
    }


def select_all(prompt):
    payload = json.loads(prompt.split("\nCONTRACT_JSON\n")[1])
    return {
        "text": json.dumps(
            {"record_ids": [r["record_id"] for r in payload["records"]]}
        ),
        "meta": {},
    }


def test_model_cannot_rewrite_source_or_add_patient_interpretation():
    c = context(
        label("Alpha increases beta exposure."), patient={"hepatic_function": "unknown"}
    )

    def bad(prompt):
        return {
            "text": json.dumps(
                {"record_ids": [], "text": "Liver function is normal; halve the dose."}
            )
        }

    result = dc.generate_doctor_answer(c, bad)
    assert "Liver function is normal" not in result["text"]
    assert "halve" not in result["text"]
    assert result["doctor_contract"]["selection_status"] == "invalid"
    assert "Alpha increases beta exposure." in result["text"]


def test_source_observation_and_applicability_remain_separate():
    c = context(
        label(
            "In patients with severe renal impairment, alpha increases beta exposure."
        ),
        patient={"renal_function": "unknown"},
    )
    result = dc.generate_doctor_answer(c, select_all)
    record = result["doctor_contract"]["records"][0]
    assert record["verification"]["source_integrity"] == "matched"
    assert record["patient_applicability"]["status"] == "missing_context"
    assert record["patient_recommendation"] is False
    assert record["relations"][0]["type"] == "supported_by"
    assert record["relations"][1]["type"] == "applicability_assessed_against"


def test_ineligible_daily_label_does_not_authorize_prescribing():
    card = label("Monitor alpha levels when beta is started.")
    card = replace(
        card,
        source_name="DailyMed SPL",
        payload={**card.payload, "action_support_eligible": False},
    )
    result = dc.generate_doctor_answer(context(card), select_all)
    assert "Monitor alpha levels" not in result["text"]
    assert result["doctor_contract"]["records"][0]["publication"] == "withheld"


def test_selection_cannot_hide_other_pair_observations_or_conflicting_sources():
    a = label("Alpha increases beta exposure.")
    b = replace(
        label("Alpha does not increase beta exposure."),
        evidence_id="ev_other",
        source_name="Other label",
    )
    result = dc.generate_doctor_answer(
        context(a, b), lambda p: {"text": '{"record_ids": []}'}
    )
    assert "Alpha increases beta exposure." in result["text"]
    assert "Alpha does not increase beta exposure." in result["text"]
    assert "agreement" in result["text"].lower()


def test_study_does_not_become_prescribing_guidance():
    card = label(
        "Alpha 15 mg was administered to 13 healthy male volunteers daily for 14 days; exposure increased by 20%."
    )
    result = dc.generate_doctor_answer(context(card), select_all)
    record = result["doctor_contract"]["records"][0]
    assert record["kind"] == "study_observation"
    assert not record["patient_recommendation"]
    assert "15 mg" not in result["text"].split("## Monitoring & Actions")[-1]


def test_prompt_uses_source_once_and_omits_unrelated_raw_graph():
    c = context(label("Alpha increases beta exposure."))
    c["reasoning_input"]["profiles"] = {"raw_response": "UNRELATED" * 10000}
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        return select_all(prompt)

    dc.generate_doctor_answer(c, generate)
    assert len(prompts) == 1
    assert len(prompts[0]) < 10000
    assert prompts[0].count("Alpha increases beta exposure.") == 1
    assert "UNRELATED" not in prompts[0]


def test_patient_facts_are_displayed_without_interpreting_inr_or_unknowns():
    c = context(
        label("Alpha increases beta exposure."),
        patient={
            "age_years": 79,
            "renal_function": "severe_impairment",
            "hepatic_function": "unknown",
            "qt_risk": "unknown",
            "current_inr": 2.4,
        },
    )
    result = dc.generate_doctor_answer(c, select_all)
    assert "Supplied renal function: severe impairment." in result["text"]
    assert "Hepatic function: not supplied." in result["text"]
    assert "Supplied INR: 2.4 (interpretation not established)." in result["text"]
    assert "therapeutic" not in result["text"].lower()


def test_literal_patient_checks_keep_complete_inspector_contract():
    from types import SimpleNamespace
    from src.domain.explanation.verifier import _check_claims

    checks = _check_claims(
        "Supplied renal function: severe impairment.",
        SimpleNamespace(drugs=["alpha", "beta"]),
        [],
        patient_context={"renal_function": "severe_impairment"},
    )
    applicability = checks[0]["patient_applicability"]
    assert checks[0]["verification_method"] == "literal_patient_field"
    assert isinstance(applicability["reasons"], list)
    assert isinstance(applicability["required_fields"], list)
    assert applicability["limitation"]


def test_withheld_guidance_does_not_claim_source_had_no_recommendation():
    card = label("Monitor alpha levels when beta is started.")
    card = replace(card, payload={**card.payload, "action_support_eligible": False})
    result = dc.generate_doctor_answer(context(card), select_all)
    assert "No management recommendation was supplied" not in result["text"]


def test_multisentence_study_survives_without_authorizing_its_regimen():
    text = "Alpha 15 mg was administered to 13 normal male volunteers following beta daily for 14 days. Exposure increased by 7%. One of 13 subjects had a two-fold response."
    card = label(text)
    card = replace(
        card,
        source_name="DailyMed SPL",
        payload={**card.payload, "action_support_eligible": False},
    )
    result = dc.generate_doctor_answer(context(card), select_all)
    assert text in result["text"]
    assert "15 mg" not in result["text"].split("## Monitoring & Actions")[-1]
