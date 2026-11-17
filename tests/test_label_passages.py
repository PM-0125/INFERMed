from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import pytest

from src.retrieval.openfda_label_client import OpenFDALabelClient
from src.utils.caching import load_json, save_json


@pytest.fixture(autouse=True)
def file_cache(monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "file")


def test_cache_retains_full_sections_raw_response_and_exact_label_provenance(
    monkeypatch, tmp_path
):
    text = "General study information. " * 120 + "Partnerbeta increases exposure."
    response = {
        "meta": {"last_updated": "2026-10-01"},
        "results": [
            {
                "id": "label-123",
                "set_id": "set-123",
                "version": "7",
                "effective_time": "20260101",
                "openfda": {"generic_name": ["ALPHADRUG"]},
                "drug_interactions": [text],
                "dosage_and_administration": ["Adjust in renal impairment."],
                "use_in_specific_populations": ["Pregnancy data are limited."],
                "precautions": ["Use caution."],
            }
        ],
    }
    client = OpenFDALabelClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client, "_request", lambda params: deepcopy(response))
    actual = client.get_label("alphadrug")
    assert actual["sections"]["drug_interactions"] == text
    assert (
        actual["sections"]["dosage_and_administration"] == "Adjust in renal impairment."
    )
    assert (
        actual["sections"]["use_in_specific_populations"]
        == "Pregnancy data are limited."
    )
    assert actual["sections"]["precautions"] == "Use caution."
    cached = load_json(tmp_path, "openfda_label__alphadrug")
    assert cached["raw_response"] == response
    assert cached["cache_schema_version"] >= 2
    assert actual["version"] == "7"
    assert parse_qs(urlparse(actual["provenance"]["source_url"]).query)["search"] == [
        'id:"label-123"'
    ]
    assert actual["provenance"]["retrieved_at"]
    monkeypatch.setattr(
        client,
        "_request",
        lambda params: pytest.fail("valid complete cache was ignored"),
    )
    assert client.get_label("alphadrug") == actual


def test_legacy_truncated_cache_refetches_even_when_fresh(monkeypatch, tmp_path):
    save_json(
        tmp_path,
        "openfda_label__alphadrug",
        {
            "query": "alphadrug",
            "found": True,
            "sections": {"drug_interactions": "Partial..."},
        },
    )
    client = OpenFDALabelClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(
        client,
        "_request",
        lambda params: {
            "results": [
                {
                    "id": "new-label",
                    "drug_interactions": [
                        "The full interaction statement is available."
                    ],
                }
            ]
        },
    )
    assert (
        client.get_label("alphadrug")["sections"]["drug_interactions"]
        == "The full interaction statement is available."
    )


def select(label, drugs, patient_context=None):
    from src.domain.evidence.label_passages import select_label_passages

    return select_label_passages(label, drugs, patient_context)


def test_late_partner_selection_preserves_negation_conditions_and_source_offsets():
    preceding = "This study enrolled adults with normal renal function."
    fact = "Partnerbeta did not increase exposure under these conditions."
    following = "These results do not establish safety in severe renal impairment."
    text = (
        "Unrelated background information. " * 700
        + preceding
        + " "
        + fact
        + " "
        + following
    )
    label = {
        "query": "alphadrug",
        "found": True,
        "label_id": "example",
        "sections": {"drug_interactions": text},
        "raw_response": {"secretly_large": text},
    }
    before = deepcopy(label)
    result = select(label, ["alphadrug", "partnerbeta"])
    assert fact in result["sections"]["drug_interactions"]
    assert preceding in result["sections"]["drug_interactions"]
    assert following in result["sections"]["drug_interactions"]
    assert result["selection"]["omitted_passages"] > 0
    assert result["selection"]["full_chars"] == len(text)
    assert result["selection"]["selected_chars"] <= 12000
    assert "raw_response" not in result
    assert label == before
    for passage in result["passages"]:
        assert passage["text"] == text[passage["start"] : passage["end"]]
        assert passage["selection_reason"]


def test_selection_balances_multiple_partners_boxed_warning_and_patient_context():
    sections = {
        "boxed_warning": "Serious bleeding can occur. Monitor coagulation regularly.",
        "drug_interactions": "\n\n".join(
            ["Partnerbeta increases exposure. " * 20] * 30
            + ["Partnergamma decreases exposure. Avoid coadministration."]
        ),
        "use_in_specific_populations": "Severe renal impairment requires additional caution.",
        "clinical_pharmacology": "Alphadrug inhibits CYP2C9 metabolism.",
    }
    result = select(
        {"query": "alphadrug", "sections": sections},
        ["alphadrug", "partnerbeta", "partnergamma"],
        {"renal_function": "severe impairment"},
    )
    rendered = "\n".join(result["sections"].values())
    assert "Partnergamma decreases exposure." in rendered
    assert "Partnerbeta increases exposure." in rendered
    assert sections["boxed_warning"] in rendered
    assert sections["use_in_specific_populations"] in rendered
    assert sections["clinical_pharmacology"] in rendered
    assert len(rendered) <= 12000
    assert result["selection"]["omitted_passages"] > 0


def test_disconnected_passages_are_explicit_and_oversize_sentences_are_not_cut():
    first = "Partnerbeta increases exposure."
    last = "Partnergamma decreases exposure."
    giant = "Unbroken " * 1600 + "."
    text = "\n\n".join([first, giant, last])
    result = select(
        {"query": "alphadrug", "sections": {"drug_interactions": text}},
        ["alphadrug", "partnerbeta", "partnergamma"],
    )
    assert (
        result["sections"]["drug_interactions"]
        == first + "\n\n[... omitted passages ...]\n\n" + last
    )
    assert result["selection"]["omitted_passages"] == 1
    assert result["selection"]["omitted_chars"] >= len(giant)


def test_full_small_label_and_missing_label_are_distinguished():
    label = {
        "query": "alphadrug",
        "found": True,
        "sections": {"warnings": "Use caution."},
    }
    full = select(label, ["alphadrug", "partnerbeta"])
    missing = select(
        {"query": "missing", "found": False, "sections": {}}, ["missing", "partnerbeta"]
    )
    assert full["sections"] == label["sections"]
    assert full["selection"]["omitted_passages"] == 0
    assert full["passages"][0]["section_complete"] is True
    assert missing["found"] is False
    assert missing["passages"] == []


def test_reselection_preserves_original_source_offsets():
    text = "Background. " * 2000 + "Partnerbeta increases exposure."
    first = select(
        {"query": "alphadrug", "sections": {"drug_interactions": text}},
        ["alphadrug", "partnerbeta"],
    )
    assert select(first, ["alphadrug", "partnerbeta"]) == first


def test_age_relevance_does_not_match_dosage_or_unknown_patient_fields():
    result = select(
        {
            "query": "alphadrug",
            "sections": {
                "dosage_and_administration": "Dosage should be individualized.",
                "warnings": "Renal impairment requires caution.",
            },
        },
        ["alphadrug"],
        {"age": 79, "renal": "unknown"},
    )
    assert all(
        "patient_context:age" not in p["selection_reason"] for p in result["passages"]
    )
    assert all(
        "patient_context:renal" not in p["selection_reason"] for p in result["passages"]
    )


def test_paragraph_boundary_preserves_short_condition_context():
    condition = "Only in adults with normal renal function:"
    fact = "Partnerbeta did not increase exposure."
    text = "\n\n".join(["Background sentence. " * 900, condition, fact])
    result = select(
        {"query": "alphadrug", "sections": {"drug_interactions": text}},
        ["alphadrug", "partnerbeta"],
    )
    partner_passage = next(p for p in result["passages"] if fact in p["text"])
    assert condition in partner_passage["text"]
