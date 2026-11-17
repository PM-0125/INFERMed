import pytest
from src.domain.explanation.action_support import enforce_action_support
from src.domain.explanation.verifier import _check_claims
from test_passage_grounding import label, decision

STUDY = (
    "In a study, alpha 15 mg was administered to 13 healthy male volunteers "
    "daily for 14 days; exposure increased by 20%."
)


def test_descriptive_study_regimen_is_preserved_without_becoming_verified_advice():
    text = STUDY + " [ev_label]"
    result = enforce_action_support(
        text, [label("Other source text.")], ["alpha", "beta"]
    )
    assert STUDY in result.text
    assert result.supported_count == 0
    claim = _check_claims(text, decision(), [label("Other source text.")])[0]
    assert claim["claim_kind"] == "study_observation"
    assert claim["status"] == "unresolved"


@pytest.mark.parametrize(
    "advice",
    [
        "Take alpha 15 mg daily.",
        "Patients should take alpha 15 mg.",
        "Therefore, use alpha 15 mg daily.",
        "Monitor alpha weekly.",
        "Recommend alpha 15 mg daily.",
    ],
)
def test_study_language_never_authorizes_added_advice(advice):
    result = enforce_action_support(
        STUDY + " " + advice, [label("")], ["alpha", "beta"]
    )
    assert advice not in result.text
    assert result.findings


def test_observation_is_not_an_escape_inside_management_section():
    result = enforce_action_support(
        "## Monitoring & Actions\n" + STUDY, [label("")], ["alpha", "beta"]
    )
    assert result.findings


def test_recorded_real_study_sentence_is_descriptive():
    text = (
        "Warfarin: There was a significant increase in prothrombin time response "
        "(area under the prothrombin time-time curve) following a single dose of warfarin "
        "(15 mg) administered to 13 normal male volunteers following oral fluconazole "
        "200 mg daily for 14 days as compared to the administration of warfarin alone."
    )
    result = enforce_action_support(text, [], ["warfarin", "fluconazole"])
    assert result.text == text
    assert result.supported_count == 0


@pytest.mark.parametrize(
    "verb",
    [
        "discontinue",
        "taper",
        "omit",
        "withhold",
        "double",
        "halve",
        "suspend",
        "refer",
        "educate",
        "screen",
    ],
)
def test_mixed_study_clause_cannot_bypass_other_action_verbs(verb):
    text = f"In 13 healthy volunteers alpha was administered daily; {verb} beta immediately."
    result = enforce_action_support(text, [], ["alpha", "beta"])
    assert result.findings
    assert text not in result.text


@pytest.mark.parametrize(
    "suffix",
    [
        ", contact your doctor immediately.",
        ", dose beta nightly.",
        "; exposure increased by 20%, contact your doctor immediately.",
    ],
)
def test_study_observation_exemption_rejects_comma_advice(suffix):
    text = "In 13 healthy volunteers alpha was administered daily" + suffix
    assert enforce_action_support(text, [], ["alpha", "beta"]).findings


@pytest.mark.parametrize(
    "advice",
    ["Restart beta at 5 mg.", "Resume beta daily.", "Beta 5 mg is appropriate."],
)
def test_multisentence_study_cannot_hide_prescribing_in_ineligible_label(advice):
    from dataclasses import replace

    text = "13 volunteers received alpha. " + advice
    card = label(text)
    card = replace(card, payload={**card.payload, "action_support_eligible": False})
    result = enforce_action_support(
        'Source guidance (alpha): "' + text + '" [ev_label]', [card], ["alpha", "beta"]
    )
    assert advice not in result.text
    assert result.findings


def test_complete_study_statistics_preserve_range_and_outlier():
    from src.domain.explanation.claim_types import is_study_observation

    text = (
        "Alpha was administered to 13 normal male volunteers following beta daily for 14 days. "
        "There was a mean ± SD increase in prothrombin time response of 7% ± 4% (range: –2 to 13%). (See PRECAUTIONS .) "
        "Mean is based on data from 12 subjects as one of 13 subjects experienced a 2-fold increase in his prothrombin time response."
    )
    assert is_study_observation(text)
