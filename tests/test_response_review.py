import json

import pytest

from src.llm.review import review_response, presentation_findings


def test_review_keeps_provenance_and_uses_same_evidence():
    draft = {"text": "No published studies exist.", "usage": {"eval_count": 7}}
    context = {"reasoning_input": {"evidence": [{"evidence_id": "ev_original"}]}}
    seen = []

    def generate(prompt):
        seen.append(prompt)
        return {
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "quote": "No published studies exist.",
                            "issue": "Overbroad absence claim",
                            "basis": "Retrieval is not exhaustive",
                        }
                    ],
                    "revised_text": "## Bottom Line\nThe retrieved evidence is limited.\n## Interaction Mechanism\nUncertain.\n## Clinical Concern\nUncertain.\n## Monitoring & Actions\nDiscuss with the prescriber.\n## Evidence Limitations\nThis search is not exhaustive.",
                }
            ),
            "usage": {"eval_count": 11},
        }

    result = review_response(draft, context, "patient", generate)
    assert "ev_original" in seen[0]
    assert result["review"]["draft"] == draft
    assert result["review"]["review_result"]["usage"]["eval_count"] == 11
    assert result["text"].startswith("## Bottom Line")
    assert result["review"]["status"] == "reviewed_not_validated"


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        '{"findings": [], "revised_text": ""}',
        '{"findings": [], "revised_text": "safe"}',
    ],
)
def test_bad_review_does_not_become_a_fabricated_success(response):
    draft = {"text": "Original response"}
    result = review_response(draft, {}, "doctor", lambda prompt: {"text": response})
    assert result["text"] == draft["text"]
    assert result["review"]["status"] == "review_failed"


def test_review_failure_is_bounded_and_preserves_draft():
    calls = []

    def fail(prompt):
        calls.append(prompt)
        raise RuntimeError("offline")

    result = review_response({"text": "Actual model draft"}, {}, "doctor", fail)
    assert len(calls) == 1
    assert result["review"]["status"] == "review_failed"
    assert result["text"] == "Actual model draft"


def test_patient_surface_checks_and_uncertainty_checks():
    findings = presentation_findings(
        "You can take them together. CYP3A4 [ev_123]",
        "patient",
        {"risk_level": "theoretical"},
    )
    assert {f["code"] for f in findings} >= {
        "patient_identifier",
        "patient_technical_language",
        "uncertain_reassurance",
    }
    assert not presentation_findings(
        "This search did not find a study. Discuss medicines with your doctor.",
        "patient",
        {},
    )


def test_generation_buffers_draft_and_calls_review_once(monkeypatch):
    from src.llm import llm_interface as llm

    monkeypatch.setenv("INFERMED_SKIP_DOTENV", "true")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    calls, chunks, stages = [], [], []
    revised = "\n".join(
        "## " + h + "\nEvidence is limited."
        for h in (
            "Bottom Line",
            "Interaction Mechanism",
            "Clinical Concern",
            "Monitoring & Actions",
            "Evidence Limitations",
        )
    )

    def provider(prompt, mode, **kwargs):
        calls.append(kwargs)
        assert kwargs["on_text"] is None
        return {
            "text": (
                "Draft"
                if len(calls) == 1
                else json.dumps({"findings": [], "revised_text": revised})
            ),
            "meta": {"provider": "ollama"},
        }

    monkeypatch.setattr(llm, "_generate_ollama_response", provider)
    result = llm.generate_response(
        {"reasoning_input": {"decision": {}}},
        "patient",
        on_text=chunks.append,
        on_stage=lambda *args: stages.append(args[0]),
    )
    assert len(calls) == 2
    assert calls[0]["raw_output"] is False
    assert calls[1]["raw_output"] is True
    assert chunks == [result["text"]]
    assert "Draft" not in result["text"]
    assert stages == ["audience_review_started", "audience_review_completed"]


def test_provider_failure_does_not_trigger_review(monkeypatch):
    from src.llm import llm_interface as llm

    monkeypatch.setenv("INFERMED_SKIP_DOTENV", "true")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        return {"text": "Unable to generate", "meta": {"error": True}}

    monkeypatch.setattr(llm, "_generate_ollama_response", provider)
    result = llm.generate_response({"reasoning_input": {"decision": {}}}, "patient")
    assert len(calls) == 1
    assert result["meta"]["error"]


def test_fabricated_reviewer_quote_is_rejected():
    revision = "\n".join(
        "## " + h + "\nUncertain."
        for h in (
            "Bottom Line",
            "Interaction Mechanism",
            "Clinical Concern",
            "Monitoring & Actions",
            "Evidence Limitations",
        )
    )
    result = review_response(
        {"text": "This is the actual draft."},
        {},
        "doctor",
        lambda p: {
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "quote": "Invented draft claim",
                            "issue": "Wrong",
                            "basis": "Evidence",
                        }
                    ],
                    "revised_text": revision,
                }
            )
        },
    )
    assert result["review"]["status"] == "review_failed"
    assert result["text"] == "This is the actual draft."


def test_heading_normalization_changes_markup_not_medical_prose():
    from src.llm.review import HEADINGS

    revised = "\n".join(
        "# " + h + "\nUncertain clinical significance." for h in HEADINGS
    )
    raw = {"text": json.dumps({"findings": [], "revised_text": revised})}
    result = review_response({"text": "draft"}, {}, "doctor", lambda p: raw)
    assert result["review"]["heading_markup_normalized"]
    assert result["review"]["review_result"] == raw
    assert result["text"] == revised.replace("# ", "## ")
