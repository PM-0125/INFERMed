from dataclasses import replace
import json
import pytest

from test_passage_grounding import label
from src.application.source_selection import passage_catalog, render_selections
from src.domain.explanation.action_support import enforce_action_support
from src.llm.review import review_response, HEADINGS


def graph(card):
    return {"evidence": [card.to_dict()], "decision": {"drugs": ["alpha", "beta"]}}


def test_selected_passage_binds_exact_text_source_and_scope():
    text = "Monitor alpha levels when beta is started or stopped."
    card = label(text)
    catalog = passage_catalog(graph(card))
    entry = next(p for p in catalog if p["text"] == text)
    rendered, audit = render_selections(entry["selection"], catalog)
    assert rendered == f'Source guidance (alpha): "{text}" [ev_label]'
    assert audit[0]["evidence_id"] == "ev_label"
    assert (
        enforce_action_support(rendered, [card], ["alpha", "beta"]).supported_count == 1
    )


def test_descriptive_dailymed_is_available_but_does_not_authorize_actions():
    card = label("Monitor alpha levels when beta is started.")
    card = replace(card, payload={**card.payload, "action_support_eligible": False})
    catalog = passage_catalog(graph(card))
    assert catalog and not catalog[0]["action_support_eligible"]
    rendered, _ = render_selections(catalog[0]["selection"], catalog)
    assert enforce_action_support(rendered, [card], ["alpha", "beta"]).findings


@pytest.mark.parametrize(
    "selection",
    ["{{source:unknown}}", "Use twice daily {{source:unknown}}", "{{source:}}"],
)
def test_unknown_or_inline_selection_rejected(selection):
    with pytest.raises(ValueError):
        render_selections(selection, [])


def test_no_scope_or_generated_card_cannot_enter_catalog():
    assert not passage_catalog(
        graph(replace(label("Alpha changes beta exposure."), drug_scope=[]))
    )
    assert not passage_catalog(
        graph(
            replace(
                label("Alpha changes beta exposure."), source_type="reasoning_engine"
            )
        )
    )


def test_selected_study_cannot_become_management_advice():
    card = label(
        "In a study, alpha 15 mg was administered to 13 healthy male volunteers daily for 14 days; exposure increased by 20%."
    )
    catalog = passage_catalog(graph(card))
    with pytest.raises(ValueError, match="Monitoring"):
        render_selections(
            "## Monitoring & Actions\n" + catalog[0]["selection"], catalog
        )


def test_model_cannot_attach_selected_label_to_its_own_quotation():
    catalog = passage_catalog(graph(label("Alpha changes beta exposure.")))
    with pytest.raises(ValueError, match="selection"):
        render_selections('"Invented statement." [ev_label]', catalog)


def test_model_cannot_append_advice_to_valid_selection():
    catalog = passage_catalog(
        graph(label("Monitor alpha levels when beta is started."))
    )
    with pytest.raises(ValueError):
        render_selections(catalog[0]["selection"] + " Then halve the dose.", catalog)


def test_passage_id_cannot_be_used_as_a_citation_for_model_prose():
    catalog = passage_catalog(graph(label("Alpha changes beta exposure.")))
    text = "No QT risk. [" + catalog[0]["passage_id"] + "]"
    with pytest.raises(ValueError):
        render_selections(text, catalog)
    rendered, audit = render_selections(text, catalog, strict=False)
    assert "sp_" not in rendered and "No QT risk" not in rendered
    assert audit[0]["status"] == "rejected"


def test_catalog_keeps_adjacent_patient_condition():
    text = "Patients with severe renal impairment require special care. Reduce alpha dose by half."
    catalog = passage_catalog(graph(label(text)))
    assert all(p["text"] == text for p in catalog)
    assert catalog[0]["claim_kind"] == "prescribing_advice"


def test_paragraph_breaks_are_retained_in_audit_and_only_whitespace_is_formatted():
    text = "Alpha affects beta exposure.\n\nThe clinical extent is uncertain."
    catalog = passage_catalog(graph(label(text)))
    assert catalog[0]["text"] == text
    rendered, audit = render_selections(catalog[0]["selection"], catalog)
    assert "Alpha affects beta exposure. The clinical extent is uncertain." in rendered
    assert audit[0]["text"] == text


def test_multisentence_patient_study_cannot_become_dosing_advice():
    catalog = passage_catalog(
        graph(
            label(
                "In 12 patients, alpha was administered at 10 mg daily. The dose was doubled after seven days."
            )
        )
    )
    with pytest.raises(ValueError):
        render_selections(
            "## Monitoring & Actions\n" + catalog[0]["selection"], catalog
        )


def test_retrieval_window_is_not_a_complete_source_boundary():
    card = label("")
    card = replace(
        card,
        payload={
            "passages": [
                {
                    "section": "warnings",
                    "text": "Discontinue alpha if necessary. Other warnings follow.",
                    "start": 156,
                    "end": 210,
                    "section_complete": False,
                }
            ]
        },
    )
    assert not passage_catalog(graph(card))


def test_complete_section_can_still_be_selected():
    card = label("")
    text = "Monitor alpha levels when beta is started."
    card = replace(
        card,
        payload={
            "passages": [
                {
                    "section": "warnings",
                    "text": text,
                    "start": 0,
                    "end": len(text),
                    "section_complete": True,
                }
            ]
        },
    )
    assert passage_catalog(graph(card))[0]["text"] == text


def test_publication_never_splits_a_selected_quote_into_partial_source_wording():
    text = "Hepatic impairment can potentiate the response to alpha. Conduct more frequent monitoring for bleeding."
    card = replace(
        label(text),
        payload={
            "sections": {"hepatic_impairment": text},
            "action_support_eligible": False,
        },
    )
    catalog = passage_catalog(graph(card))
    rendered, _ = render_selections(catalog[0]["selection"], catalog)
    published = enforce_action_support(rendered, [card], ["alpha", "beta"])
    assert "Hepatic impairment can" not in published.text
    assert "Source guidance" not in published.text
    assert len(published.findings) == 1


@pytest.mark.parametrize(
    "heading",
    ["## Management", "## Recommendations", "## Monitoring & Actions\n### Evidence"],
)
def test_study_role_cannot_escape_via_headings(heading):
    catalog = passage_catalog(
        graph(label("In 12 healthy volunteers, alpha was administered at 10 mg daily."))
    )
    with pytest.raises(ValueError):
        render_selections(heading + "\n" + catalog[0]["selection"], catalog)


def test_review_materializes_selection_and_retains_audit():
    card = label("Monitor alpha levels when beta is started.")
    ctx = {"reasoning_input": graph(card)}
    entry = passage_catalog(ctx["reasoning_input"])[0]
    body = "\n".join(
        "## "
        + h
        + "\n"
        + (
            entry["selection"]
            if h == "Monitoring & Actions"
            else "Evidence is limited."
        )
        for h in HEADINGS
    )
    answer = review_response(
        {"text": body},
        ctx,
        "doctor",
        lambda p: {"text": json.dumps({"findings": [], "revised_text": body})},
    )
    assert "{{source:" not in answer["text"]
    assert entry["text"] in answer["text"]
    assert answer["review"]["source_selections"][0]["passage_id"] == entry["passage_id"]


def test_exhausted_selection_retry_withholds_invalid_line_but_preserves_valid_source():
    card = label("Monitor alpha levels when beta is started.")
    ctx = {"reasoning_input": graph(card)}
    entry = passage_catalog(ctx["reasoning_input"])[0]
    body = "\n".join(
        "## "
        + h
        + "\n"
        + (
            entry["selection"]
            if h == "Monitoring & Actions"
            else "{{source:ev_invented}}"
        )
        for h in HEADINGS
    )
    answer = review_response(
        {"text": body},
        ctx,
        "doctor",
        lambda p: {"text": json.dumps({"findings": [], "revised_text": body})},
    )
    assert answer["review"]["attempts"] == 2
    assert answer["review"]["status"] == "review_required"
    assert entry["text"] in answer["text"]
    assert "ev_invented" not in answer["text"]
    assert any(
        p.get("status") == "rejected" for p in answer["review"]["source_selections"]
    )
