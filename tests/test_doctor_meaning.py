from dataclasses import replace
import pytest
from test_passage_grounding import label, decision
from src.domain.explanation.verifier import _check_claims


def fda():
    return replace(
        label(""),
        claim_type="mechanism_reference",
        evidence_grade="mechanistic",
        source_name="FDA CYP/transporter reference",
        payload={
            "matches": [
                {"row": {"Enzyme": "CYP2C9", "Moderate index inhibitors": "alpha(e)"}}
            ]
        },
    )


@pytest.mark.parametrize(
    "text",
    ["Alpha is a moderate inhibitor of CYP2C9.", "Alpha moderately inhibits CYP2C9."],
)
def test_structured_relation_verifies_meaning_not_metadata_equality(text):
    claim = _check_claims(text + " [ev_label]", decision(), [fda()])[0]
    assert claim["status"] == "supported"
    assert claim["verification_method"] == "structured_relation"
    assert "Moderate index inhibitors" in claim["source_matches"][0]["text"]


@pytest.mark.parametrize(
    "text",
    ["Alpha is a strong inhibitor of CYP2C9.", "Alpha strongly inhibits CYP2C9."],
)
def test_conflicting_strength_is_contradicted(text):
    assert (
        _check_claims(text + " [ev_label]", decision(), [fda()])[0]["status"]
        == "contradicted"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Alpha moderately inhibits CYP3A4.",
        "Beta moderately inhibits CYP2C9.",
        "Alpha moderately inhibits CYP2C9 and is safe for this patient.",
        "No evidence shows alpha moderately inhibits CYP2C9.",
    ],
)
def test_unrelated_or_extended_proposition_is_never_approved(text):
    assert (
        _check_claims(text + " [ev_label]", decision(), [fda()])[0]["status"]
        != "supported"
    )


def test_engine_absence_heuristic_is_not_verified_absence_of_pd_interaction():
    card = replace(
        label(""),
        claim_type="pd_summary",
        source_type="mechanism_engine",
        claim_text="No obvious PD overlap",
        drug_scope=["alpha", "beta"],
    )
    claim = _check_claims(
        "There is no pharmacodynamic overlap between alpha and beta. [ev_label]",
        decision(),
        [card],
    )[0]
    assert claim["status"] == "unsupported"
    assert "absence" in " ".join(claim["reasons"])


def test_study_equivalence_preserves_every_number_population_and_condition():
    source = "Alpha 15 mg was administered to 13 healthy male volunteers following beta 200 mg administered daily for 14 days."
    quote = source.replace("200 mg administered daily", "200 mg daily")
    card = replace(label(source), drug_scope=["alpha", "beta"])
    claim = _check_claims(quote + " [ev_label]", decision(), [card])[0]
    assert claim["status"] == "supported"
    assert claim["verification_method"] == "study_regimen_equivalence"
    for bad in [
        quote.replace("200 mg", "20 mg"),
        quote.replace("male", "female"),
        quote.replace("healthy", "severely ill"),
        quote.replace("was administered", "was not administered"),
    ]:
        assert (
            _check_claims(bad + " [ev_label]", decision(), [card])[0]["status"]
            != "supported"
        )
