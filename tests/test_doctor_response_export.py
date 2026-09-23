from scripts.build_offline_mode_samples import ASSESSMENTS, PROVENANCE
from scripts.export_doctor_responses import _markdown_html, _packet_config, _render_html


def test_markdown_response_renders_lists_and_emphasis() -> None:
    rendered = _markdown_html(
        "- **Interaction likelihood:** Unclear\n"
        "- **Interaction type:**\n"
        "  * **PK:** *No strong PK overlap detected*"
    )

    assert "<ul>" in rendered
    assert "<strong>Interaction likelihood:</strong>" in rendered
    assert "<strong>PK:</strong>" in rendered
    assert "<em>No strong PK overlap detected</em>" in rendered
    assert "**" not in rendered


def test_html_keeps_research_metadata_but_marks_doctor_pdf_exclusions() -> None:
    summary = {
        "generated_at": "2026-08-11T00:00:00+00:00",
        "case_count": 1,
        "success_count": 1,
        "failure_count": 0,
    }
    rows = [
        {
            "ok": True,
            "elapsed_s": 10.0,
            "case": {
                "case_id": "CASE-1",
                "drugs": ["drug-a", "drug-b"],
                "case_group": "double",
                "knowledge_status": "doctor-provided",
                "primary_domain": "test",
                "expected_risk_clusters": "test risk",
                "expected_severity_band": "unknown",
                "key_context_modifiers": "none",
                "evaluation_target": "clinical usefulness",
                "ground_truth_status": "requires review",
            },
            "display": {
                "risk": {
                    "label": "Unknown",
                    "confidence": "Low",
                    "interactionClass": "Evidence review",
                },
                "assessment": [
                    {"title": "Bottom Line", "body": "- **Result:** Review evidence."}
                ],
                "evidence": {
                    "overview": {
                        "metrics": [{"label": "Risk", "value": "Unknown"}],
                        "rows": [
                            {"title": "PK summary", "description": "No strong overlap."}
                        ],
                    },
                    "openfda": {
                        "metrics": [],
                        "rows": [],
                        "caveat": "Associative signal only.",
                    },
                    "references": [
                        {
                            "title": "OpenFDA caveat",
                            "description": "Not proof of causality.",
                            "meta": "FAERS",
                        }
                    ],
                },
            },
        }
    ]

    rendered = _render_html(summary, rows)

    assert "Knowledge status" in rendered
    assert "Evaluation target" in rendered
    assert "Ground truth status" in rendered
    assert "class='pdf-excluded'><span>Class</span>" in rendered
    assert "class='pdf-excluded'><span>Runtime</span>" in rendered
    assert ".pdf-excluded, .screen-only { display: none !important; }" in rendered
    assert "<strong>Result:</strong>" in rendered
    assert "class='evidence-metric'" in rendered
    assert "class='evidence-row'" in rendered
    assert "References and caveats" in rendered
    assert 'content: "\\2022"' in rendered


def test_offline_provider_provenance_is_visible() -> None:
    summary = {
        "generated_at": "2026-08-11T00:00:00+00:00",
        "audience": "pv_research",
        "case_count": 1,
        "success_count": 1,
        "failure_count": 0,
    }
    rows = [
        {
            "ok": True,
            "elapsed_s": 0.0,
            "case": {
                "case_id": "CASE-1",
                "drugs": ["drug-a", "drug-b"],
                "case_group": "double",
                "knowledge_status": "doctor-provided",
                "primary_domain": "test",
                "expected_risk_clusters": "test risk",
                "expected_severity_band": "unknown",
                "key_context_modifiers": "none",
                "evaluation_target": "clinical usefulness",
                "ground_truth_status": "requires review",
            },
            "display": {
                "risk": {
                    "label": "Unknown",
                    "confidence": "Low",
                    "interactionClass": "Evidence review",
                },
                "assessment": [
                    {"title": "Bottom Line", "body": "Evidence remains insufficient."}
                ],
                "evidence": {},
                "response_provenance": {
                    "provider": "codex_offline_sandboxed",
                    "label": "Codex Offline (Sandboxed)",
                    "note": "Evidence-only synthesis.",
                },
            },
        }
    ]

    rendered = _render_html(summary, rows)

    assert "INFERMed Pharmacovigilance and Research Mode Responses" in rendered
    assert "class='provider-banner'" in rendered
    assert "Codex Offline (Sandboxed)" in rendered
    assert "Evidence-only synthesis." in rendered


def test_packet_filenames_follow_audience() -> None:
    assert _packet_config("doctor")["pdf_filename"] == "doctor_mode_response.pdf"
    assert _packet_config("patient")["pdf_filename"] == "patient_mode_response.pdf"
    assert (
        _packet_config("pv_research")["pdf_filename"]
        == "pharmacovigilance_research_mode_response.pdf"
    )


def test_reviewed_offline_assessments_are_complete_and_non_prescriptive() -> None:
    required_titles = [
        "Bottom Line",
        "Interaction Mechanism",
        "Clinical Concern",
        "Monitoring & Actions",
        "Evidence Limitations",
    ]
    prohibited = ("reduce the dose", "increase the dose", "stop taking", "switch to")

    assert PROVENANCE["label"] == "Codex Offline (Sandboxed)"
    assert set(ASSESSMENTS) == {
        ("patient", "DOC-DDI2-001"),
        ("pv_research", "DOC-DDI2-001"),
        ("pv_research", "DOC-DDI3-001"),
        ("pv_research", "DOC-DDI4-001"),
    }
    for assessment in ASSESSMENTS.values():
        assert [section["title"] for section in assessment] == required_titles
        combined = "\n".join(section["body"] for section in assessment).lower()
        assert len(combined) >= 800
        assert not any(phrase in combined for phrase in prohibited)
