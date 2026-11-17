from pathlib import Path

from src.domain.decision.scoring import score_pair_context


def test_shared_protein_is_not_automatically_moderate():
    ctx = {"pkpd": {"pk_detail": {"overlaps": {"shared": ["CYP3A4"]}}}}
    assert score_pair_context(ctx)[:2] == ("theoretical", "low")


def test_reporting_signal_alone_does_not_establish_major_risk():
    ctx = {
        "signals": {"tabular": {"prr": 100}, "faers": {"combo_reactions": ["event"]}}
    }
    assert score_pair_context(ctx)[:2] == ("theoretical", "low")


def test_empty_nested_overlap_is_missing_evidence():
    assert (
        score_pair_context({"pkpd": {"pk_detail": {"overlaps": {"x": [[]]}}}})[0]
        == "insufficient_evidence"
    )


def test_explicit_canonical_severity_is_not_downgraded():
    ctx = {"pkpd": {"pk_detail": {"canonical_interaction": {"severity": "high"}}}}
    assert score_pair_context(ctx)[0] == "major"


def test_directional_roles_and_prodrug_uncertainty():
    from src.domain.reasoning.interpretation import interpret_pair

    ctx = {
        "signals": {
            "mechanistic": {
                "enzymes": {
                    "a": {"inhibitor": ["CYP2C19"]},
                    "b": {"substrate": ["CYP2C19"]},
                }
            }
        },
        "pkpd": {"pk_summary": "No strong PK overlap detected"},
    }
    result = interpret_pair(ctx, ["a", "b"])
    chain = result["causal_chains"][0]
    assert (chain["perpetrator"], chain["affected_drug"], chain["action"]) == (
        "a",
        "b",
        "inhibition",
    )
    assert "activation" in chain["effect_caveat"]
    assert chain["clinical_consequence"] == "not established"
    assert result["conflicts"]


def test_cosubstrates_do_not_get_directional_claim():
    from src.domain.reasoning.interpretation import interpret_pair

    ctx = {
        "signals": {
            "mechanistic": {
                "enzymes": {
                    "a": {"substrate": ["CYP3A4"]},
                    "b": {"substrate": ["CYP3A4"]},
                }
            }
        }
    }
    assert not interpret_pair(ctx, ["a", "b"])["causal_chains"]


def test_doctor_prompt_does_not_order_verbatim_negative_overlap():
    text = Path("src/llm/prompt_templates.txt").read_text(encoding="utf-8")
    assert "PK summary verbatim" not in text
    assert 'Example format: "DATA-DRIVEN PK OVERLAP: No strong' not in text


def test_background_is_classified_without_being_called_verified():
    from src.domain.explanation.verifier import _check_claims
    from src.domain.decision.entities import InteractionDecision

    decision = InteractionDecision(
        "t", "pair", ["a", "b"], "theoretical", "low", "insufficient"
    )
    claim = _check_claims(
        "General pharmacology knowledge: inhibition may reduce metabolism.",
        decision,
        [],
    )[0]
    assert claim["knowledge_basis"] == "background_knowledge"
    assert claim["status"] == "unresolved"


def test_display_uses_same_decision_as_generation():
    from src.api.transformers import calculate_risk_summary

    ctx = {
        "reasoning_input": {
            "decision": {"risk_level": "theoretical", "confidence": "low"}
        },
        "signals": {"tabular": {"prr": 100}},
    }
    assert calculate_risk_summary(ctx) == (
        "unknown",
        "Clinical Severity Unestablished",
        "low",
    )


def test_gene_digits_do_not_become_quantitative_dose_advice():
    from src.domain.explanation.verifier import _check_claims
    from src.domain.decision.entities import InteractionDecision

    decision = InteractionDecision(
        "t", "pair", ["a", "b"], "theoretical", "low", "insufficient"
    )
    result = _check_claims(
        "General pharmacology knowledge: CYP2C19 inhibition may increase exposure.",
        decision,
        [],
    )
    assert result[0]["status"] == "unresolved"
