import json
from src.llm.review import review_response, HEADINGS

REVISION = "\n".join("## " + h + "\nEvidence is limited." for h in HEADINGS)


def test_valid_draft_excerpt_id_avoids_reconstructed_quote():
    def generate(prompt):
        assert "draft_0001" in prompt
        return {
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "excerpt_id": "draft_0001",
                            "issue": "Unsupported alternative",
                            "basis": "No applicable label",
                        }
                    ],
                    "revised_text": REVISION,
                }
            )
        }

    result = review_response(
        {"text": "Consider an alternative."}, {}, "doctor", generate
    )
    assert result["review"]["status"] == "reviewed_not_validated"
    assert result["review"]["findings"][0]["quote"] == "Consider an alternative."


def test_invalid_quote_has_one_repair_attempt_and_keeps_both_responses():
    calls = []

    def generate(prompt):
        calls.append(prompt)
        return {
            "text": json.dumps(
                {
                    "findings": (
                        [
                            {
                                "quote": "Fabricated quote",
                                "issue": "Issue",
                                "basis": "Missing support",
                            }
                        ]
                        if len(calls) == 1
                        else [
                            {
                                "excerpt_id": "draft_0001",
                                "issue": "Issue",
                                "basis": "Missing support",
                            }
                        ]
                    ),
                    "revised_text": REVISION,
                }
            )
        }

    result = review_response(
        {"text": "Actual unsupported draft."}, {}, "doctor", generate
    )
    assert len(calls) == 2
    assert result["review"]["attempts"] == 2
    assert len(result["review"]["attempt_history"]) == 2
    assert result["review"]["status"] == "reviewed_not_validated"


def test_invalid_excerpt_id_never_substitutes_fabricated_text():
    result = review_response(
        {"text": "Actual draft."},
        {},
        "doctor",
        lambda p: {
            "text": json.dumps(
                {
                    "findings": [
                        {"excerpt_id": "draft_9999", "issue": "Issue", "basis": "Basis"}
                    ],
                    "revised_text": REVISION,
                }
            )
        },
    )
    assert result["review"]["status"] == "review_failed"
    assert result["text"] == "Actual draft."
