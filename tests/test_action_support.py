from dataclasses import replace

import pytest

from src.domain.evidence.entities import EvidenceCard


def card(text="Monitor alpha levels weekly.", **kwargs):
    fields = dict(
        evidence_id="ev_label",
        analysis_id="test",
        source_name="openFDA Drug Label API",
        source_type="public_api",
        claim_type="label_context",
        claim_text="Label retrieved",
        evidence_grade="label",
        drug_scope=["alpha"],
        payload={"sections": {"drug_interactions": text}},
    )
    fields.update(kwargs)
    return EvidenceCard(**fields)


def enforce(text, cards=None, review_status=None):
    from src.domain.explanation.action_support import enforce_action_support

    return enforce_action_support(
        text, cards or [], ["alpha", "beta"], review_status=review_status
    )


@pytest.mark.parametrize(
    "action",
    [
        "Check INR twice weekly.",
        "Repeat the test every other day.",
        "Reduce the dose if INR > 3.0.",
        "Reduce the dose if INR ≥ 3·0.",
        "Monitor potassium closely.",
        "No dose adjustment is required.",
        "An alternative antibiotic should be prescribed.",
        "Consider switching to another antifungal.",
        "Avoid this combination.",
    ],
)
def test_unsupported_actions_are_withheld(action):
    result = enforce("## Monitoring & Actions\n" + action)
    assert action not in result.text
    assert result.status == "recommendations_withheld"
    assert result.findings


@pytest.mark.parametrize(
    "action",
    [
        "Check INR twice weekly.",
        "The dose should be reduced.",
        "Avoid this combination.",
    ],
)
def test_actions_cannot_escape_by_moving_outside_action_section(action):
    result = enforce("## Bottom Line\n" + action)
    assert action not in result.text


def test_numeric_observation_and_biology_are_not_recommendations():
    text = "## Clinical Concern\nThe patient is 79 and has INR 2.4.\nAlpha inhibits CYP3A4 and increases beta exposure."
    result = enforce(text)
    assert result.text == text
    assert result.status == "no_actionable_recommendations"


def test_complete_scoped_label_statement_is_retained():
    text = "## Monitoring & Actions\nMonitor alpha levels weekly. [ev_label]"
    result = enforce(text, [card()])
    assert result.text == text
    assert result.status == "source_text_matched"


@pytest.mark.parametrize(
    "bad_card",
    [
        card(drug_scope=["gamma"]),
        card(evidence_grade="pharmacovigilance_signal"),
        card(source_type="reasoning_engine"),
        card(text="Do not monitor alpha levels weekly."),
        card(text="If severe toxicity develops, monitor alpha levels weekly."),
    ],
)
def test_wrong_scope_grade_negation_or_dropped_condition_does_not_support_action(
    bad_card,
):
    result = enforce(
        "## Monitoring & Actions\nMonitor alpha levels weekly. [ev_label]", [bad_card]
    )
    assert "Monitor alpha levels weekly." not in result.text


def test_citation_does_not_support_another_sentence_in_the_paragraph():
    text = "## Monitoring & Actions\nMonitor alpha levels weekly. [ev_label] Increase beta to 5 mg."
    result = enforce(text, [card()])
    assert "Increase beta" not in result.text
    assert result.status == "recommendations_withheld"


def test_unknown_reference_and_source_name_alone_do_not_authorize_action():
    for citation in ("[ev_missing]", "(FDA label)"):
        result = enforce(
            "## Monitoring & Actions\nMonitor alpha levels weekly. " + citation,
            [card()],
        )
        assert result.status == "recommendations_withheld"


def test_review_failure_is_explicit_and_cannot_release_actions():
    result = enforce(
        "## Monitoring & Actions\nMonitor alpha levels weekly. [ev_label]",
        [card()],
        "review_failed",
    )
    assert "Monitor alpha levels weekly." not in result.text
    assert result.status == "review_failed"
    assert "review" in result.text.lower() and "withheld" in result.text.lower()


def test_readable_doctor_citations_are_resolved_from_metadata():
    from src.domain.explanation.presentation import render_citations

    cards = [
        card(),
        replace(
            card(),
            evidence_id="ev_signal",
            source_name="FAERS",
            evidence_grade="pharmacovigilance_signal",
        ),
    ]
    text = "Mechanism [ev_label]. Signal [ev_signal]. Unknown [ev_missing]."
    shown = render_citations(text, cards, "doctor")
    assert "ev_" not in shown
    assert "alpha prescribing information" in shown.lower()
    assert "FAERS" in shown
    assert "Source unavailable" in shown
    assert render_citations(text, cards, "pv_research") == text


def test_citation_rendering_handles_grouped_and_bare_ids_without_fabricating_links():
    from src.domain.explanation.presentation import render_citations

    shown = render_citations(
        "[ev_label, ev_label]; ev_label; [ev_missing]", [card()], "doctor"
    )
    assert "ev_" not in shown
    assert "http" not in shown


def test_source_link_is_not_promoted_from_an_unsafe_url():
    from src.domain.explanation.presentation import render_citations

    shown = render_citations(
        "[ev_label]", [card(provenance={"url": "javascript:alert(1)"})], "doctor"
    )
    assert "javascript" not in shown


@pytest.mark.parametrize(
    "text",
    [
        "## Bottom Line\nAn ECG is warranted.",
        "## Clinical Concern\nThe warfarin dose should be halved.",
        "## Take beta at bedtime\nEvidence is limited.",
        "## Bottom Line\nObtain an ECG.",
        "## Bottom Line\nStop alpha immediately.",
        "## Bottom Line\nDouble warfarin tonight.",
        "## Bottom Line\nContinue both medicines unchanged.",
    ],
)
def test_management_wording_and_headings_do_not_bypass_gate(text):
    assert enforce(text).status == "recommendations_withheld"


def test_mechanism_section_is_not_an_action_section():
    text = (
        "## Interaction Mechanism\nAlpha inhibits CYP3A4 and increases beta exposure."
    )
    assert enforce(text).text == text


@pytest.mark.parametrize(
    "biology", ["Alpha can increase beta exposure.", "Alpha may reduce beta clearance."]
)
def test_modal_pharmacology_is_not_a_management_instruction(biology):
    text = "## Interaction Mechanism\n" + biology
    assert enforce(text).text == text


def test_missing_management_evidence_remains_visible_as_a_limitation():
    text = (
        "## Evidence Limitations\nNo monitoring schedule was supplied by the sources."
    )
    assert enforce(text).text == text


def test_missing_evidence_statement_cannot_smuggle_an_instruction():
    text = "## Evidence Limitations\nNo monitoring schedule was supplied by the sources. Stop alpha."
    assert "Stop alpha" not in enforce(text).text


@pytest.mark.parametrize(
    "instruction",
    [
        "Reduce alpha by 50%.",
        "Increase alpha by 25%.",
        "Decrease alpha by half.",
        "Alpha 10 mg at night.",
        "Return for bloodwork in 48 hours.",
        "Use alpha.",
        "Alpha 5mg nightly.",
        "You can reduce alpha by half.",
    ],
)
def test_quantitative_and_implicit_regimens_require_support(instruction):
    assert (
        enforce("## Bottom Line\n" + instruction).status == "recommendations_withheld"
    )


def test_missing_dose_adjustment_data_is_an_evidence_limitation():
    text = "## Evidence Limitations\nNo dose adjustment data were retrieved."
    assert enforce(text).text == text


def test_truncated_source_cannot_authorize_a_recommendation():
    result = enforce(
        "## Monitoring & Actions\nAvoid alpha... [ev_label]",
        [card(text="Avoid alpha...")],
    )
    assert result.status == "recommendations_withheld"


def test_source_prerequisite_in_previous_sentence_cannot_be_dropped():
    source = card(
        text="The following instructions apply only to severe renal impairment. Avoid alpha."
    )
    result = enforce("## Monitoring & Actions\nAvoid alpha. [ev_label]", [source])
    assert result.status == "recommendations_withheld"


def test_implicit_drug_scope_cannot_be_assigned_to_another_drug_heading():
    result = enforce(
        "## Beta dosing\nReduce the dose by 50%. [ev_label]",
        [card(text="Reduce the dose by 50%.")],
    )
    assert result.status == "recommendations_withheld"


def test_full_source_passage_retains_condition_and_target():
    passage = "For patients with severe renal impairment, avoid alpha. The restriction applies during beta treatment."
    text = "## Monitoring & Actions\n" + passage + " [ev_label]"
    assert (
        enforce(text, [card(text=passage, drug_scope=["alpha", "beta"])]).text == text
    )


def test_evidence_only_sentence_stays_attached_to_the_claim():
    from src.domain.explanation.verifier import _check_claims
    from src.domain.decision.entities import InteractionDecision

    evidence = card(text="Monitor alpha levels weekly.")
    evidence = replace(
        evidence,
        claim_text="Alpha is a substrate.",
        evidence_grade="mechanistic",
        claim_type="enzyme_role",
    )
    decision = InteractionDecision(
        analysis_id="test",
        drugs=["alpha", "beta"],
        interaction_scope="pair",
        risk_level="theoretical",
        confidence="low",
        evidence_grade="mechanistic",
        recommended_action="counsel",
    )
    claims = _check_claims(
        "Alpha is a substrate. Evidence: [ev_label].", decision, [evidence]
    )
    assert len(claims) == 1
    assert claims[0]["status"] == "supported"
    assert claims[0]["drug_scope"] == ["alpha"]


def test_publication_gate_applies_to_analysis_and_retains_original_audit(tmp_path):
    from src.application.commands import AnalyzeMedicationSetCommand
    from src.application.use_cases.analyze_medication_set import (
        AnalyzeMedicationSetUseCase,
    )
    from src.infrastructure.persistence.event_store import SQLiteEventStore

    store = SQLiteEventStore(tmp_path / "events.sqlite")
    original = "## Monitoring & Actions\nCheck INR twice weekly. [ev_fake]"
    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {},
        answer_generator=lambda *a, **k: {"text": original},
        event_store=store,
    ).execute(AnalyzeMedicationSetCommand(medications=["alpha", "beta"]))
    assert "twice weekly" not in result.rag_output["answer"]["text"]
    assert result.verification["publication"]["status"] == "recommendations_withheld"
    saved = store.get_analysis_snapshot(result.analysis_id)["response_read_model"][
        "answer"
    ]
    assert saved["publication_audit"]["original_text"] == original
    assert "ev_fake" not in str(result.to_read_model()["verification"])


def test_doctor_analysis_citation_uses_actual_card_name(tmp_path):
    from src.application.commands import AnalyzeMedicationSetCommand
    from src.application.use_cases.analyze_medication_set import (
        AnalyzeMedicationSetUseCase,
    )
    from src.infrastructure.persistence.event_store import SQLiteEventStore

    def generate(ctx, *args, **kwargs):
        ref = ctx["reasoning_input"]["evidence"][0]["evidence_id"]
        return {"text": f"Evidence is limited. [{ref}]"}

    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {},
        answer_generator=generate,
        event_store=SQLiteEventStore(tmp_path / "events.sqlite"),
    ).execute(AnalyzeMedicationSetCommand(medications=["alpha", "beta"]))
    assert "ev_" not in result.rag_output["answer"]["text"]
    assert "INFERMed pair reasoner" in result.rag_output["answer"]["text"]


def test_followup_is_gated_and_does_not_leak_internal_references():
    from src.application.use_cases.answer_followup import (
        AnswerFollowUpCommand,
        AnswerFollowUpUseCase,
    )

    recorded = []
    result = AnswerFollowUpUseCase(
        followup_generator=lambda *a, **k: {
            "text": "Check INR twice weekly. [ev_fake]"
        },
        context_runner=lambda *a, **k: {},
        cited_cards_from_context=lambda ctx: [],
        explanation_recorder=lambda **record: recorded.append(record),
    ).execute(AnswerFollowUpCommand(question="Monitoring?", drugs=["alpha", "beta"]))
    assert "twice weekly" not in result.answer
    assert "ev_" not in result.answer
    assert result.to_dict()["publication"]["status"] == "recommendations_withheld"
    assert recorded[0]["answer_text"] == "Check INR twice weekly. [ev_fake]"
    assert (
        recorded[0]["verification"]["publication"]["status"]
        == "recommendations_withheld"
    )
