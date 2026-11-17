import pytest


def test_invented_inr_number_cannot_borrow_supplied_value():
    text = "INR 5 is therapeutic."
    result = enforce_action_support(
        text,
        [],
        ["warfarin", "fluconazole"],
        patient_context={
            "current_inr": 2.4,
            "inr_target_range": [2, 3],
            "indication": "AF",
        },
    )
    assert text not in result.text


from src.domain.explanation.action_support import enforce_action_support
from src.domain.explanation.applicability import assess_applicability


@pytest.mark.parametrize(
    "text",
    [
        "Current INR 2.4 is near the upper therapeutic limit for many indications.",
        "Patient-specific amplifiers: 79-year-old, severe renal impairment, current INR = 2.4 (within a typical therapeutic range).",
        "The current INR is therapeutic.",
    ],
)
def test_unprovided_inr_target_never_publishes_interpretation(text):
    result = enforce_action_support(
        text, [], ["warfarin", "fluconazole"], patient_context={"current_inr": 2.4}
    )
    assert text not in result.text
    assert result.findings[0]["reason"].startswith("Patient interpretation")


def test_contradictory_inr_interpretation_is_not_matched():
    result = assess_applicability(
        "The current INR is therapeutic.",
        {"current_inr": 5, "inr_target_range": [2, 3], "indication": "supplied"},
    )
    assert result["status"] == "condition_conflict"


@pytest.mark.parametrize(
    "text",
    [
        "In patients with severe renal impairment, the current INR is normal.",
        "INR 5 is therapeutic.",
    ],
)
def test_other_matching_condition_cannot_validate_inr(text):
    result = enforce_action_support(
        text,
        [],
        ["warfarin", "fluconazole"],
        patient_context={
            "current_inr": 5,
            "inr_target_range": [2, 3],
            "indication": "AF",
            "renal_function": "severe_impairment",
        },
    )
    assert text not in result.text


def test_observed_inr_value_survives_without_interpretation():
    text = "Current INR: 2.4; indication and target range were not supplied."
    result = enforce_action_support(
        text, [], ["warfarin", "fluconazole"], patient_context={"current_inr": 2.4}
    )
    assert result.text == text


def test_patient_specific_causal_assertion_needs_source():
    text = "Age 79: Elderly patients have reduced hepatic clearance and heightened sensitivity to warfarin."
    result = enforce_action_support(
        text, [], ["warfarin", "fluconazole"], patient_context={"age_years": 79}
    )
    assert text not in result.text
