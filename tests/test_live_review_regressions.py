import pytest


def test_unrelated_external_label_cannot_rescue_absence_claim():
    text = "The interaction is purely pharmacokinetic."
    result = enforce_action_support(
        text + " [ev_label]",
        [label("Alpha increases beta exposure.")],
        ["alpha", "beta"],
    )
    assert text not in result.text


from src.domain.explanation.action_support import enforce_action_support
from src.domain.explanation.verifier import _check_claims
from test_passage_grounding import label, decision


def test_caution_about_exclusively_pk_is_not_an_absence_claim():
    text = "The interaction is not purely pharmacokinetic."
    result = enforce_action_support(text, [], ["alpha", "beta"], patient_context={})
    assert result.text == text


def test_internal_patient_claim_cannot_support_itself():
    from dataclasses import replace

    text = "The patient has higher alpha exposure due to renal impairment."
    card = replace(
        label(text), source_type="reasoning_engine", evidence_grade="mechanistic"
    )
    result = enforce_action_support(
        text + " [ev_label]",
        [card],
        ["alpha", "beta"],
        patient_context={"renal_function": "severe_impairment"},
    )
    assert text not in result.text


def test_complete_source_statement_wrapper_is_only_formatting():
    text = "Alpha increases beta exposure only under the studied conditions."
    claim = _check_claims(
        '- **Source statement:** "' + text + '" [ev_label]', decision(), [label(text)]
    )[0]
    assert claim["status"] == "supported"


def test_source_statement_wrapper_does_not_hide_added_claim():
    text = "Alpha increases beta exposure only under the studied conditions."
    claim = _check_claims(
        '- **Source statement:** "' + text + '" Therefore safe. [ev_label]',
        decision(),
        [label(text)],
    )
    assert not all(c["status"] == "supported" for c in claim)


@pytest.mark.parametrize(
    "text",
    [
        "- **Interpretation:** The patient is 79 years old and has severe renal impairment; both factors are associated with higher bleeding risk.",
        "- **Interpretation:** Age and severe renal impairment may increase bleeding risk; however, specific evidence for additive effect with this interaction is lacking.",
        "- **Interpretation:** Renal impairment can affect fluconazole exposure and potentially magnify the interaction.",
        "- **Interpretation:** The risk is driven solely by pharmacokinetic changes; no additional pharmacodynamic synergy.",
        "- **Interpretation:** The two drugs act on distinct targets; the interaction is purely pharmacokinetic.",
    ],
)
def test_live_unsupported_extrapolations_are_withheld(text):
    result = enforce_action_support(
        text,
        [],
        ["warfarin", "fluconazole"],
        patient_context={"age_years": 79, "renal_function": "severe_impairment"},
    )
    assert text not in result.text
