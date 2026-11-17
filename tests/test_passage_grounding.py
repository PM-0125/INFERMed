from dataclasses import replace

import pytest

from src.domain.decision.entities import InteractionDecision
from src.domain.evidence.entities import EvidenceCard
from src.domain.explanation.verifier import _check_claims
from src.domain.explanation.action_support import enforce_action_support
from src.domain.explanation.presentation import present_verification


def label(text):
    return EvidenceCard(
        evidence_id="ev_label",
        analysis_id="test",
        source_name="Drug Label",
        source_type="public_label_api",
        claim_type="label_context",
        claim_text="Alpha label sections returned",
        evidence_grade="label",
        drug_scope=["alpha"],
        payload={"sections": {"warnings": text}},
    )


def decision():
    return InteractionDecision(
        analysis_id="test",
        drugs=["alpha", "beta"],
        interaction_scope="pair",
        risk_level="major",
        confidence="high",
        evidence_grade="label",
        recommended_action="monitor",
    )


def test_complete_sentence_in_source_field_has_visible_support():
    cards = [
        label(
            "Alpha can cause bleeding. Monitor alpha levels regularly. Report any bleeding."
        )
    ]
    claims = _check_claims(
        "Monitor alpha levels regularly. [ev_label]", decision(), cards
    )
    assert claims[0]["status"] == "supported"
    assert claims[0]["source_matches"][0]["text"] == "Monitor alpha levels regularly."
    assert claims[0]["source_matches"][0]["section"] == "warnings"


def test_risk_factor_number_is_not_a_quantitative_instruction():
    text = "Age over 65 can increase alpha bleeding risk."
    claims = _check_claims(text + " [ev_label]", decision(), [label(text)])
    assert claims[0]["status"] == "supported"
    assert not any("Quantitative action" in r for r in claims[0]["reasons"])


def test_formatting_is_not_a_claim_and_nested_subject_is_inherited():
    card = replace(
        label(""), claim_text="Alpha inhibits CYP2C9.", claim_type="enzyme_role"
    )
    claims = _check_claims(
        "## Mechanism\n- **Actor:** Alpha\n  - Alpha inhibits CYP2C9. [ev_label]\n1. **Patient modifiers**\nDisclaimer: Research prototype.",
        decision(),
        [card],
    )
    assert len(claims) == 1
    assert claims[0]["status"] == "supported"


@pytest.mark.parametrize(
    "source",
    [
        "Only in severe renal impairment. Monitor alpha levels regularly.",
        "Monitor alpha levels regularly. This recommendation applies only in severe renal impairment.",
        "Do not monitor alpha levels regularly.",
        "If toxicity develops, monitor alpha levels regularly.",
    ],
)
def test_dropped_qualifiers_never_pass(source):
    claims = _check_claims(
        "Monitor alpha levels regularly. [ev_label]", decision(), [label(source)]
    )
    assert claims[0]["status"] != "supported"
    result = enforce_action_support(
        "## Monitoring & Actions\nMonitor alpha levels regularly. [ev_label]",
        [label(source)],
        ["alpha", "beta"],
    )
    assert result.status == "recommendations_withheld"


def test_supported_action_survives_separate_unsupported_sentence():
    text = "## Monitoring & Actions\nMonitor alpha levels regularly. [ev_label] Increase beta to 5 mg."
    result = enforce_action_support(
        text,
        [label("Alpha can cause bleeding. Monitor alpha levels regularly.")],
        ["alpha", "beta"],
    )
    assert "Monitor alpha levels regularly." in result.text
    assert "Increase beta" not in result.text
    assert result.supported_count == 1


def test_presentation_does_not_obscure_published_claims_when_another_action_withheld():
    claims = _check_claims(
        "Alpha can cause bleeding. [ev_label]",
        decision(),
        [label("Alpha can cause bleeding.")],
    )
    shown = present_verification(
        {"claims": claims, "publication": {"status": "recommendations_withheld"}},
        [label("")],
        "doctor",
    )
    assert shown["claims"][0]["text"] == "Alpha can cause bleeding."
    assert "ev_" not in str(shown)


def test_uncited_paraphrase_is_not_certified_by_keyword_overlap():
    claims = _check_claims(
        "Alpha never causes bleeding. [ev_label]",
        decision(),
        [label("Alpha can cause bleeding.")],
    )
    assert claims[0]["status"] != "supported"


def test_analysis_checks_published_text_and_keeps_draft_checks_in_audit(tmp_path):
    from src.application.use_cases.analyze_medication_set import (
        AnalyzeMedicationSetUseCase,
    )
    from src.application.commands import AnalyzeMedicationSetCommand
    from src.infrastructure.persistence.event_store import SQLiteEventStore

    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {},
        answer_generator=lambda *a, **k: {
            "text": "Alpha inhibits CYP2C9.\nMonitor alpha every 2 days."
        },
        event_store=SQLiteEventStore(tmp_path / "events.sqlite"),
    ).execute(AnalyzeMedicationSetCommand(medications=["alpha", "beta"]))
    assert all("every 2 days" not in c["text"] for c in result.verification["claims"])
    assert result.rag_output["answer"]["publication_audit"]["draft_claims"]


def test_faers_single_drug_cards_cannot_authorize_pair_counts():
    from src.application.use_cases.analyze_medication_set import (
        evidence_cards_from_context,
    )

    cards = evidence_cards_from_context(
        "test",
        {
            "signals": {
                "faers": {
                    "top_reactions_a": [["INR INCREASED", 5389]],
                    "combo_reactions": [["BLEEDING", 12]],
                }
            }
        },
        ["alpha", "beta"],
    )
    singles = [
        c
        for c in cards
        if c.source_name == "OpenFDA FAERS" and c.drug_scope == ["alpha"]
    ]
    assert singles
    assert all(
        "5389" not in str(c.payload) for c in cards if c.drug_scope == ["alpha", "beta"]
    )


@pytest.mark.parametrize(
    "builder_name", ["build_prompt", "build_compact_analysis_prompt"]
)
def test_doctor_contract_requires_decision_relevant_depth(builder_name):
    from src.llm import llm_interface

    prompt = getattr(llm_interface, builder_name)({}, "doctor")
    for term in (
        "active metabolites",
        "target range",
        "onset",
        "source-supported facts",
        "patient-specific interpretation",
    ):
        assert term in prompt


def test_review_reports_unfixed_finding():
    import json
    from src.llm.review import review_response, HEADINGS

    bad = "Check alpha every 2 days."
    revised = "\n".join(
        "## "
        + h
        + "\n"
        + (bad if h == "Monitoring & Actions" else "Evidence is limited.")
        for h in HEADINGS
    )
    result = review_response(
        {"text": bad},
        {},
        "doctor",
        lambda p: {
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "quote": bad,
                            "issue": "Unsupported interval",
                            "basis": "No source",
                        }
                    ],
                    "revised_text": revised,
                }
            )
        },
    )
    assert result["review"]["status"] == "review_required"
    assert any(
        f["code"] == "review_finding_retained"
        for f in result["review"]["remaining_findings"]
    )


def test_generation_receives_late_partner_passage_without_raw_snapshot(tmp_path):
    from src.application.use_cases.analyze_medication_set import (
        AnalyzeMedicationSetUseCase,
    )
    from src.application.commands import AnalyzeMedicationSetCommand
    from src.infrastructure.persistence.event_store import SQLiteEventStore

    source = {
        "query": "alpha",
        "found": True,
        "raw_response": {"secret_sentinel": "raw_snapshot"},
        "sections": {
            "drug_interactions": "Unrelated background. " * 900
            + "Alpha increases beta exposure. Monitor alpha levels."
        },
    }

    def generate(ctx, *a, **k):
        cards = ctx["reasoning_input"]["evidence"]
        card = next(c for c in cards if c["claim_type"] == "label_context")
        assert "raw_response" not in str(ctx)
        assert "Alpha increases beta exposure." in str(card["payload"])
        assert card["payload"]["selection"]["omitted_chars"] > 0
        return {"text": "Evidence is limited."}

    AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {
            "signals": {"clinical_reference": {"openfda_label": {"a": source}}}
        },
        answer_generator=generate,
        event_store=SQLiteEventStore(tmp_path / "events.sqlite"),
    ).execute(AnalyzeMedicationSetCommand(medications=["alpha", "beta"]))


def test_old_assembled_context_does_not_keep_truncated_label_and_unsafe_roles():
    from src.llm.rag_pipeline import _context_cache_version_ok

    assert not _context_cache_version_ok({"meta": {"version": 12}})


def test_assay_context_is_not_presented_as_clinical_validation():
    from src.llm.llm_interface import _format_chembl_enrichment
    from src.api.transformers import _chembl_rows

    value = {
        "a": {
            "chembl_validation": {"found": True, "matches": ["CYP2C9"]},
            "bioactivities": [
                {
                    "enzyme": "CYP2C9",
                    "potency_type": "IC50",
                    "potency_value": "5",
                    "potency_units": "nM",
                    "action": "unknown",
                }
            ],
        }
    }
    for text in (_format_chembl_enrichment(value), str(_chembl_rows(value))):
        assert "validated" not in text
        assert "nM" in text
        assert "clinical" in text.lower()


def test_when_condition_in_neighbour_cannot_be_dropped():
    source = label(
        "When toxicity develops, reassess treatment. Monitor alpha levels regularly."
    )
    text = "Monitor alpha levels regularly. [ev_label]"
    assert _check_claims(text, decision(), [source])[0]["status"] != "supported"
    assert (
        enforce_action_support(text, [source], ["alpha", "beta"]).status
        == "recommendations_withheld"
    )


def test_exact_pair_guidance_in_single_product_label_can_pass():
    source = label("Monitor alpha levels during beta therapy.")
    text = "Monitor alpha levels during beta therapy. [ev_label]"
    assert _check_claims(text, decision(), [source])[0]["status"] == "supported"
    assert (
        enforce_action_support(text, [source], ["alpha", "beta"]).status
        == "source_text_matched"
    )


def test_explicit_label_scope_allows_generic_source_instruction_without_dose_ambiguity():
    source = label("Perform regular monitoring of INR in all treated patients.")
    text = 'Source guidance (alpha): "Perform regular monitoring of INR in all treated patients." [ev_label]'
    assert _check_claims(text, decision(), [source])[0]["status"] == "supported"
    assert (
        enforce_action_support(text, [source], ["alpha", "beta"]).status
        == "source_text_matched"
    )
    assert (
        enforce_action_support(
            text.replace("(alpha)", "(beta)"), [source], ["alpha", "beta"]
        ).status
        == "recommendations_withheld"
    )


def test_conditional_added_instruction_does_not_remove_independently_supported_action():
    source = label("Monitor alpha levels regularly.")
    text = "Monitor alpha levels regularly. [ev_label] If bleeding develops, increase beta to 5 mg."
    result = enforce_action_support(text, [source], ["alpha", "beta"])
    assert "Monitor alpha levels regularly." in result.text
    assert "increase beta" not in result.text


def test_selection_cannot_detach_following_conditional_paragraph():
    from src.domain.evidence.label_passages import select_label_passages

    text = (
        "Monitor alpha levels regularly.\n\n"
        + "This recommendation applies only in severe renal impairment. "
        + "Population context. " * 45
        + "\n\n"
        + "Neutral material. " * 55
        + "\n\n"
        + "\n\n".join("Alpha inhibits metabolism. " * 33 for _ in range(13))
    )
    payload = select_label_passages(
        {"query": "alpha", "sections": {"warnings": text}}, ["alpha", "beta"]
    )
    card = replace(label(""), payload=payload)
    result = enforce_action_support(
        "Monitor alpha levels regularly. [ev_label]", [card], ["alpha", "beta"]
    )
    assert result.status == "recommendations_withheld"


def test_persisted_explanation_record_matches_its_published_checks(tmp_path):
    from src.application.use_cases.analyze_medication_set import (
        AnalyzeMedicationSetUseCase,
    )
    from src.application.commands import AnalyzeMedicationSetCommand
    from src.infrastructure.persistence.event_store import SQLiteEventStore

    records = []
    store = SQLiteEventStore(tmp_path / "events.sqlite")
    store.append_explanation_record = lambda **kw: records.append(kw)
    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=lambda *a, **k: {},
        answer_generator=lambda *a, **k: {"text": "Monitor alpha every 2 days."},
        event_store=store,
    ).execute(AnalyzeMedicationSetCommand(medications=["alpha", "beta"]))
    assert records[0]["answer_text"] == result.rag_output["answer"]["text"]
    assert records[0]["verification"]["checked_text"] == "published_narrative"


def test_prompt_uses_selected_passages_once_and_keeps_omission_metadata():
    from src.application.reasoning_input import render_reasoning_input

    payload = {
        "evidence": [
            {
                "payload": {
                    "sections": {"warnings": "unique source sentinel"},
                    "passages": [
                        {"text": "unique source sentinel", "section": "warnings"}
                    ],
                    "selection": {"omitted_chars": 200},
                    "raw_response": {"extra": "raw sentinel"},
                }
            }
        ]
    }
    text = render_reasoning_input(payload)
    assert text.count("unique source sentinel") == 1
    assert "raw sentinel" not in text
    assert '"omitted_chars": 200' in text


def test_doctor_prompt_prioritizes_citable_label_statements_over_graph_payloads():
    from src.application.reasoning_input import render_reasoning_input

    payload = {
        "decision": {"drugs": ["alpha", "beta"]},
        "evidence": [
            label("Alpha can cause bleeding. Monitor alpha levels regularly.").to_dict()
        ],
        "profiles": {
            "nodes": [
                {"type": "identifier", "payload": {"unused_identity_sentinel": "noise"}}
            ],
            "edges": [],
        },
        "patient_context": {"current_inr": 2.4},
    }
    text = render_reasoning_input(payload, "doctor")
    assert "source_statement_examples" in text
    assert "Source guidance (alpha)" in text
    assert "unused_identity_sentinel" not in text
    assert "profile_projection" in text
    assert '"current_inr": 2.4' in text


def test_review_quote_wrappers_are_normalized_but_unmatched_findings_stay_flagged():
    import json
    from src.llm.review import review_response, HEADINGS

    revised = "\n".join("## " + h + "\nEvidence remains limited." for h in HEADINGS)
    result = review_response(
        {"text": "Actual draft wording."},
        {},
        "doctor",
        lambda p: {
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "quote": '"Actual draft wording."',
                            "issue": "Too vague",
                            "basis": "Source",
                        },
                        {
                            "quote": "A paraphrase not in the draft.",
                            "issue": "Uncertain claim",
                            "basis": "Source",
                        },
                    ],
                    "revised_text": revised,
                }
            )
        },
    )
    assert result["review"]["status"] == "review_required"
    assert any(
        f["code"] == "review_quote_unmatched"
        for f in result["review"]["remaining_findings"]
    )
    assert result["review"]["findings"][0]["quote"] == "Actual draft wording."


def test_complete_conditional_sentence_with_citation_is_not_rejected_for_containing_if():
    source = label(
        "Alpha affects beta exposure. Monitor alpha levels if beta is prescribed. Report bleeding."
    )
    text = "Monitor alpha levels if beta is prescribed. [ev_label]"
    assert (
        enforce_action_support(text, [source], ["alpha", "beta"]).status
        == "source_text_matched"
    )


def test_unicode_citation_delimiters_and_quoted_sentence_are_resolved():
    from src.domain.explanation.presentation import render_citations

    text = "“Monitor alpha levels regularly.”【ev_label】."
    assert (
        enforce_action_support(
            text, [label("Monitor alpha levels regularly.")], ["alpha", "beta"]
        ).status
        == "source_text_matched"
    )
    assert "【" not in render_citations(text, [label("")], "doctor")


def test_doctor_projection_preserves_directional_edges_between_retained_nodes():
    from src.application.reasoning_input import doctor_context

    edge = {"source_id": "drug:alpha", "target_id": "enzyme:2c9", "type": "inhibits"}
    result = doctor_context(
        {
            "profiles": {
                "nodes": [
                    {"node_id": "drug:alpha", "type": "drug"},
                    {"node_id": "enzyme:2c9", "type": "enzyme"},
                ],
                "edges": [edge],
            }
        }
    )
    assert result["profiles"]["edges"] == [edge]


def test_source_examples_do_not_prioritize_other_pairs_from_interaction_section():
    from src.application.reasoning_input import doctor_context

    card = replace(
        label(""),
        payload={
            "sections": {
                "drug_interactions": "Alpha raises gamma exposure. Beta raises alpha exposure."
            }
        },
    )
    result = doctor_context(
        {"decision": {"drugs": ["alpha", "beta"]}, "evidence": [card.to_dict()]}
    )
    examples = str(result["source_statement_examples"])
    assert "gamma" not in examples
    assert "Beta raises alpha" in examples


def test_inline_source_guidance_survives_unverified_commentary_and_section_crossref():
    source = label(
        "Perform regular monitoring of INR in all treated patients [see Dosage and Administration (2.1)]."
    )
    text = "- **INR monitoring** – Source guidance (alpha): “Perform regular monitoring of INR in all treated patients.” [ev_label] – Clinicians should check twice weekly."
    result = enforce_action_support(text, [source], ["alpha", "beta"])
    assert "Perform regular monitoring" in result.text
    assert "twice weekly" not in result.text
    assert result.supported_count == 1


def test_abbreviated_descriptive_quote_is_partial_never_full_support():
    source = label("Alpha can cause bleeding in patients with severe renal impairment.")
    text = "Bleeding risk: “Alpha can cause bleeding … severe renal impairment.” [ev_label]"
    claim = _check_claims(text, decision(), [source])[0]
    assert claim["status"] == "partial"
    assert claim["source_matches"][0]["text"] == source.payload["sections"]["warnings"]


def test_abbreviated_management_quote_still_withheld():
    source = label("Monitor alpha weekly only if renal impairment is severe.")
    text = "Source guidance (alpha): “Monitor alpha weekly … severe.” [ev_label]"
    assert (
        enforce_action_support(text, [source], ["alpha", "beta"]).status
        == "recommendations_withheld"
    )


def test_cited_fda_inhibition_strength_conflict_is_not_merely_unresolved():
    card = replace(
        label(""),
        claim_type="mechanism_reference",
        evidence_grade="mechanistic",
        source_name="FDA CYP/transporter reference",
        payload={
            "matches": [
                {
                    "row": {
                        "Enzyme": "CYP2C9",
                        "Moderate index inhibitors": "alpha(e)",
                        "Strong index inhibitors": "-",
                    }
                }
            ]
        },
    )
    claim = _check_claims(
        "Alpha strongly inhibits CYP2C9. [ev_label]", decision(), [card]
    )[0]
    assert claim["status"] == "contradicted"
    assert "moderate" in " ".join(claim["reasons"]).lower()


def test_pair_quote_from_single_product_label_can_be_partial_with_typographic_hyphen():
    source = replace(
        label(
            "In post-marketing experience, bleeding events including bruising were reported with alpha concurrently with beta."
        ),
        drug_scope=["alpha"],
    )
    text = "Source guidance (alpha): “In post‑marketing experience, bleeding events … were reported with alpha concurrently with beta.” [ev_label]"
    assert _check_claims(text, decision(), [source])[0]["status"] == "partial"


def test_two_product_labels_do_not_automatically_fail_collective_scope():
    a = replace(label("Alpha can cause bleeding."), drug_scope=["alpha"])
    b = replace(
        label("Beta changes exposure."), evidence_id="ev_beta", drug_scope=["beta"]
    )
    result = _check_claims(
        "Alpha and beta may increase bleeding risk. [ev_label] [ev_beta]",
        decision(),
        [a, b],
    )[0]
    assert result["status"] == "unresolved"
    assert not result["source_matches"]


def test_inline_bold_qualifier_is_never_silently_dropped():
    source = label("Monitor alpha levels regularly.")
    text = '**Only in severe renal impairment** - Source guidance (alpha): "Monitor alpha levels regularly." [ev_label]'
    result = enforce_action_support(text, [source], ["alpha", "beta"])
    assert result.status == "recommendations_withheld"
    assert result.supported_count == 0
    assert "Only in severe renal impairment" in result.findings[0]["text"]


def test_inline_quote_survives_independent_advice_without_losing_trailing_qualification():
    source = label("Monitor alpha levels regularly.")
    base = '**INR monitoring** - Source guidance (alpha): "Monitor alpha levels regularly." [ev_label]'
    independent = (
        base
        + " - Clinicians may consider more frequent monitoring, but no algorithm is provided."
    )
    result = enforce_action_support(independent, [source], ["alpha", "beta"])
    assert result.supported_count == 1
    assert "more frequent" not in result.text
    qualified = base + " - Only if severe renal impairment is present."
    assert (
        enforce_action_support(qualified, [source], ["alpha", "beta"]).supported_count
        == 0
    )


def test_rejection_of_wrong_enzyme_strength_is_not_itself_contradicted():
    from src.domain.explanation.passages import fda_strength_conflicts

    card = replace(
        label(""),
        claim_type="mechanism_reference",
        payload={
            "matches": [
                {"row": {"Enzyme": "CYP2C9", "Moderate index inhibitors": "alpha(e)"}}
            ]
        },
    )
    assert not fda_strength_conflicts(
        "It is incorrect to claim that alpha strongly inhibits CYP2C9.", [card]
    )
