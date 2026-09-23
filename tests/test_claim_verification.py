import pytest
from src.domain.decision.entities import InteractionDecision
from src.domain.evidence.entities import EvidenceCard
from src.domain.explanation.verifier import ExplanationVerifier
from src.domain.safety.entities import SafetyReport


def verify(text, scope=None, claim="Monitor for bleeding.", risk="major"):
    card = EvidenceCard(
        "ev_abc123",
        "test",
        "label",
        "label",
        "monitoring",
        claim,
        "label",
        scope or ["a", "b"],
    )
    decision = InteractionDecision("test", "pair", ["a", "b"], risk, "high", "label")
    return (
        ExplanationVerifier()
        .verify(
            answer_text=text,
            decision=decision,
            evidence_cards=[card],
            safety_report=SafetyReport(
                analysis_id="test", allow_generation=True, requires_review=False
            ),
        )
        .to_dict()
    )


@pytest.mark.parametrize(
    "text",
    [
        "Monitor for bleeding. [ev_invented]",
        "Reduce the dose by 50%. [ev_abc123]",
        "No interaction; safe to combine. [ev_abc123]",
    ],
)
def test_unsupported_or_contradictory_claims_require_review(text):
    result = verify(text)
    assert result["status"] == "review_required"
    assert any(c["status"] in {"unsupported", "contradicted"} for c in result["claims"])


def test_wrong_scope_does_not_support_claim():
    assert (
        verify("Monitor for bleeding. [ev_abc123]", scope=["a", "c"])["status"]
        == "review_required"
    )


def test_exact_evidence_claim_passes_narrow_checks():
    assert verify("Monitor for bleeding. [ev_abc123]")["status"] == "checks_passed"


def test_uncited_prose_is_unresolved_not_verified():
    assert verify("An interaction is possible.")["claims"][0]["status"] == "unresolved"


def test_negated_causality_is_not_flagged_as_causal_proof():
    from src.domain.safety.services import _CAUSAL_OVERCLAIM_RE

    assert not _CAUSAL_OVERCLAIM_RE.search("FAERS does not prove causality.")
