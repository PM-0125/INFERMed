from dataclasses import replace
from test_passage_grounding import label
from test_doctor_contract import context, select_all
from src.application.doctor_contract import generate_doctor_answer
from src.domain.explanation.meaning import verify_meaning

STUDY = (
    "A pharmacokinetic study was conducted in 22 subjects, 65 years of age or older receiving a single 50 mg oral dose of alpha. "
    "In addition, creatinine clearance (74 mL/min), the percent of drug recovered unchanged in urine (22%), "
    "and the alpha renal clearance estimates (0.124 mL/min/kg) for the elderly were generally lower than those of younger volunteers."
)
CLAIM = "The alpha pharmacokinetic study in 22 subjects aged 65 years or older reported creatinine clearance of 74 mL/min, generally lower than in younger volunteers."


def study_card(text=STUDY, complete=True):
    card = label(text)
    return replace(
        card,
        source_name="DailyMed SPL",
        drug_scope=["alpha"],
        payload={
            **card.payload,
            "action_support_eligible": False,
            "passages": [
                {
                    "section": "clinical_pharmacology",
                    "text": text,
                    "section_complete": complete,
                }
            ],
        },
    )


def test_reported_renal_field_retains_cohort_source_context_without_a_prescription():
    card = study_card()
    meaning = verify_meaning(CLAIM, [card], ["alpha"])
    assert meaning and meaning[0] == "supported"
    assert meaning[1] == "structured_study_field"
    assert meaning[2][0]["context"] == STUDY
    result = generate_doctor_answer(
        context(card, patient={"renal_function": "severe_impairment"}),
        select_all,
        question="Renal function?",
    )
    assert CLAIM in result["text"]
    assert "50 mg" not in result["text"]
    assert any(
        r["kind"] == "structured_study_observation" and r["publication"] == "accepted"
        for r in result["doctor_contract"]["records"]
    )


def test_renal_field_never_certifies_wrong_value_unit_population_or_patient_claim():
    card = study_card()
    for wrong in [
        CLAIM.replace("74", "47"),
        CLAIM.replace("mL/min", "mL/min/kg"),
        CLAIM.replace("22", "23"),
        CLAIM.replace("65", "18"),
        CLAIM.replace("lower", "higher"),
        "This patient has creatinine clearance of 74 mL/min.",
    ]:
        check = verify_meaning(wrong, [card], ["alpha"])
        assert not check or check[0] != "supported"


def test_incomplete_or_ambiguous_study_cannot_authorize_a_field():
    for card in [
        study_card(complete=False),
        study_card(
            STUDY
            + " In addition, creatinine clearance (12 mL/min) was reported elsewhere."
        ),
        study_card(
            STUDY.replace(
                "A pharmacokinetic study was conducted",
                "No pharmacokinetic study was conducted",
            )
        ),
    ]:
        check = verify_meaning(CLAIM, [card], ["alpha"])
        assert not check or check[0] != "supported"


def test_threshold_or_switched_cohort_cannot_be_certified():
    header, endpoint = STUDY.split("In addition, ")
    for text in [
        header
        + "In addition, creatinine clearance (74 mL/min) was a prespecified exclusion threshold; observed values for the elderly were generally lower than those of younger volunteers.",
        header
        + "The following measurements refer to a separate group of 12 elderly subjects. In addition, "
        + endpoint,
        "The following data are hypothetical. " + STUDY,
        STUDY + " These measurements were subsequently corrected.",
    ]:
        check = verify_meaning(CLAIM, [study_card(text)], ["alpha"])
        assert not check or check[0] != "supported"


def test_synthetic_study_retains_measurement_and_rejects_unknown_context():
    from pathlib import Path
    from src.domain.explanation.study_fields import renal_study_fields

    text = (Path(__file__).parent / "fixtures/synthetic_renal_study.txt").read_text(
        encoding="utf-8"
    )
    facts = renal_study_fields(study_card(text))
    assert len(facts) == 1
    assert facts[0]["population_n"] == "18"
    assert facts[0]["minimum_age_years"] == "70"
    assert facts[0]["study_dose_mg"] == "15"
    assert facts[0]["value"] == "61"
    assert facts[0]["unit"] == "mL/min"
    assert (
        facts[0]["text"]
        == "The alpha pharmacokinetic study in 18 subjects aged 70 years or older reported creatinine clearance of 61 mL/min, generally lower than in younger volunteers."
    )
    assert facts[0]["source_context"] == text
    for changed in [
        text.replace(
            "In addition,",
            "The next measurements concern a different cohort. In addition,",
        ),
        text + " Correction: the measured value was different.",
    ]:
        assert renal_study_fields(study_card(changed)) == []
