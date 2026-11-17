from test_doctor_contract import context, select_all
from test_passage_grounding import label
from src.application.doctor_contract import generate_doctor_answer


def test_renal_followup_addresses_population_and_excludes_unrelated_material():
    study = label(
        "Alpha 15 mg was administered to 13 normal male volunteers following beta daily for 14 days. Exposure increased by 7%."
    )
    unrelated = label("Alpha can cause rash.")
    result = generate_doctor_answer(
        context(study, unrelated, patient={"renal_function": "severe_impairment"}),
        select_all,
        question="What does renal impairment mean for this case?",
    )
    assert "## Direct Answer" in result["text"]
    assert "renal function" in result["text"]
    assert "study population" in result["text"].lower()
    assert "Alpha can cause rash." not in result["text"]
    assert result["doctor_contract"]["question_plan"]["topic"] == "renal"


def test_renal_followup_retains_opposing_findings_even_if_model_omits_them():
    a = label("Alpha clearance is reduced in renal impairment.")
    b = label("Alpha clearance is unchanged in renal impairment.")
    result = generate_doctor_answer(
        context(a, b),
        lambda p: {"text": '{"record_ids": []}'},
        question="Renal function?",
    )
    records = result["doctor_contract"]["records"]
    assert len(
        result["doctor_contract"]["question_plan"]["relevant_record_ids"]
    ) == len(records)
    assert all(
        r["record_id"]
        in result["doctor_contract"]["question_plan"]["relevant_record_ids"]
        for r in records
    )


def test_renal_missing_evidence_is_not_negative_finding_or_default_dose():
    result = generate_doctor_answer(
        context(label("Alpha can cause rash.")),
        select_all,
        question="Renal impairment?",
    )
    assert (
        "No accepted retrieved record in this review directly addresses renal function"
        in result["text"]
    )
    assert "absence of a renal effect" in result["text"]
    assert "halve" not in result["text"].lower()


def test_unrecognized_question_is_not_silently_answered_with_generic_note():
    result = generate_doctor_answer(
        context(label("Alpha can cause rash.")),
        select_all,
        question="What is the cost?",
    )
    assert "question is outside" in result["text"]


def test_renal_aliases_and_numeric_condition_records_cannot_disappear():
    from src.application.doctor_questions import plan_question

    records = [
        {
            "record_id": "a",
            "text": "Renal impairment changes exposure.",
            "patient_applicability": {},
        },
        {
            "record_id": "b",
            "text": "CrCl below 30 changes exposure.",
            "patient_applicability": {},
        },
        {
            "record_id": "c",
            "text": "This source reports the opposite result.",
            "patient_applicability": {
                "checked_conditions": [{"field": "creatinine_clearance_ml_min"}]
            },
        },
    ]
    assert plan_question("CrCl concerns?", records)["relevant_record_ids"] == [
        "a",
        "b",
        "c",
    ]
    assert plan_question("QTc risk?", [])["topics"] == ["qt"]


def test_withheld_record_metadata_does_not_look_like_new_management_advice():
    from src.application.doctor_questions import question_intro
    from src.domain.explanation.action_support import enforce_action_support

    records = [
        {
            "source_name": "DailyMed SPL",
            "section": "geriatric_use",
            "drug_scope": ["alpha"],
            "publication": "withheld",
            "text": "renal context",
            "patient_applicability": {},
            "verification": {"prescribing_authority": "not_established"},
        }
    ]
    text = "\n".join(question_intro({"topics": ["renal"]}, records))
    assert enforce_action_support(text, [], ["alpha", "beta"]).findings == []


def test_renal_question_distinguishes_category_from_supplied_lab_fields():
    result = generate_doctor_answer(
        context(
            label("Alpha can cause rash."),
            patient={"renal_function": "severe_impairment"},
        ),
        select_all,
        question="Renal function?",
    )
    assert "No creatinine-clearance or eGFR field was supplied" in result["text"]
    result = generate_doctor_answer(
        context(
            label("Alpha can cause rash."),
            patient={"labs": [{"name": "eGFR", "value": 20, "unit": "mL/min/1.73m2"}]},
        ),
        select_all,
        question="Renal function?",
    )
    assert "No creatinine-clearance or eGFR field was supplied" not in result["text"]
