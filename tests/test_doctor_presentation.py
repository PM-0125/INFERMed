from src.domain.explanation.presentation import render_citations


def test_doctor_note_emits_real_lists_and_bold_labels_after_verification():
    text = "## Bottom Line\nSummary.\n\n## Patient Context\nSupplied age in years: 79.\nSupplied renal function: severe impairment.\n\n## Clinical Concern\nOne observation.\nAnother observation."
    result = render_citations(text, [], "doctor")
    assert "## Clinical Summary" in result and "Bottom Line" not in result
    assert "- **Supplied age in years:** 79." in result
    assert "- One observation.\n- Another observation." in result
    assert "## Patient Context\n\n" in result


def test_existing_markdown_and_non_doctor_format_are_preserved():
    text = "## Context\n\n- **Existing** item\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    assert render_citations(text, [], "doctor") == text
    assert (
        render_citations("## Bottom Line\nText.", [], "patient")
        == "## Bottom Line\nText."
    )
