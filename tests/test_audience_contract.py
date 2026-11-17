from src.llm.llm_interface import build_prompt, build_compact_analysis_prompt
import pytest


@pytest.mark.parametrize("builder", [build_prompt, build_compact_analysis_prompt])
@pytest.mark.parametrize("mode", ["patient", "doctor", "pv_research"])
def test_audience_contract_is_present_in_each_generation_path(builder, mode):
    prompt = builder({}, mode)
    assert "AUDIENCE CONTRACT" in prompt
    assert "Audience changes explanation, not evidence certainty" in prompt
    expected = {
        "patient": "Use everyday words",
        "doctor": "actor -> enzyme/transporter or target",
        "pv_research": "confirmation and falsification",
    }
    assert expected[mode] in prompt
    if mode == "patient":
        assert "Usually safe" not in prompt
        assert "No tables" in prompt
    if mode == "pv_research":
        assert "confounding by indication" in prompt


def test_compact_prompt_does_not_force_clinician_report_for_patient():
    assert "clinician-facing report" not in build_compact_analysis_prompt({}, "patient")


@pytest.mark.parametrize("builder", [build_prompt, build_compact_analysis_prompt])
def test_patient_graph_does_not_demand_technical_citations(builder):
    prompt = builder(
        {"reasoning_input": {"decision": {"confidence": "low"}}}, "patient"
    )
    assert "Cite supporting evidence IDs in square brackets" not in prompt
    assert "how these medicines usually work" in prompt
    assert "not permission to take" in prompt


@pytest.mark.parametrize("mode", ["patient", "doctor", "pv_research"])
def test_no_legacy_forced_reassurance_or_management(mode):
    prompt = build_prompt({}, mode)
    for obsolete in (
        "Reassure without minimizing",
        "Category: Low / Moderate / High",
        "Dose adjustment may be needed in some patients",
        "consider periodic LFT/ECG monitoring",
    ):
        assert obsolete not in prompt


def test_research_requires_testable_not_merely_elaborate_hypotheses():
    prompt = build_prompt({}, "pv_research")
    assert "confirmation and falsification" in prompt
    assert "competing explanation" in prompt
    assert "not permission to take" in prompt
