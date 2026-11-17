from pathlib import Path
import xml.etree.ElementTree as ET
from src.retrieval.dailymed_client import DailyMedClient
from src.retrieval.spl_units import split_text_units
from test_source_passage_enrichment import XML


def test_table_section_preserves_prose_cells_spans_caption_and_footnotes():
    table = '<table><caption>Observed values</caption><thead><tr><th>Population</th><th>Clearance (mL/min)</th></tr></thead><tbody><tr><td rowspan="2">Studied group</td><td>25</td></tr><tr><td>30*</td></tr></tbody><tfoot><tr><td colspan="2">*Only the studied population.</td></tr></tfoot></table>'
    xml = XML.replace(
        "</text>", table + "<paragraph>Do not extrapolate the table.</paragraph></text>"
    )
    label = DailyMedClient.parse_spl(
        xml,
        "alpha",
        {"set_id": "11111111-1111-1111-1111-111111111111", "spl_version": "3"},
    )
    assert "drug_interactions" in label["sections"]
    retained = label["tables"][0]
    assert retained["caption"] == "Observed values"
    assert retained["rows"][1]["cells"][0]["rowspan"] == "2"
    assert retained["rows"][-1]["group"] == "tfoot"
    assert "Only the studied population" in retained["rows"][-1]["cells"][0]["text"]
    assert "Do not extrapolate the table." in label["source_units"][0]["text"]
    assert label["source_units"][0]["content_kind"] == "structured_table_section"
    assert retained["action_support_eligible"] is False


def synthetic_body():
    return ET.fromstring(
        (
            Path(__file__).parent / "fixtures/synthetic_interaction_sections.xml"
        ).read_text(encoding="utf-8")
    )


def test_synthetic_hierarchy_preserves_sections_and_group_context():
    hierarchy = {
        "alpha": [],
        "beta": [],
        "example group": [],
        "gamma": ["example group"],
        "delta": ["example group"],
    }
    body = synthetic_body()
    units = split_text_units(
        body, ["Example interactions"], [], heading_hierarchy=hierarchy
    )
    assert len(units) == 5
    alpha = next(u for u in units if u["section_path"][-1] == "Alpha:")
    assert "Alpha observation" in alpha["text"]
    assert "Beta observation" not in alpha["text"]
    gamma = next(u for u in units if u["section_path"][-1] == "Gamma:")
    assert gamma["section_path"] == ["Example interactions", "Example group:", "Gamma:"]
    assert (
        "Group findings concern only the simulated study population." in gamma["text"]
    )
    assert "Delta observation" not in gamma["text"]
    assert gamma["boundary_policy"] == "reviewed_content_bound_hierarchy"
    del hierarchy["delta"]
    # A missing expected heading also prevents a guessed split.
    hierarchy["missing"] = []
    fallback = split_text_units(
        body, ["Example interactions"], [], heading_hierarchy=hierarchy
    )
    assert len(fallback) == 1
    assert "Alpha observation" in fallback[0]["text"]
    assert "Delta observation" in fallback[0]["text"]


def test_reviewed_layout_fingerprint_rejects_unknown_drug_and_changed_body(monkeypatch):
    import hashlib
    from src.retrieval import spl_layouts

    body = synthetic_body()
    assert spl_layouts.reviewed_hierarchy(body, "fluconazole") is None
    # Replace only the expected digest for this test. Production fingerprints stay unchanged.
    monkeypatch.setattr(
        spl_layouts,
        "FLUCONAZOLE_INTERACTION_BODY",
        hashlib.sha256(ET.tostring(body, encoding="utf-8")).hexdigest(),
    )
    hierarchy = spl_layouts.reviewed_hierarchy(body, "fluconazole")
    assert hierarchy["glipizide"] == ["oral hypoglycemics"]
    assert hierarchy["warfarin"] == []
    assert spl_layouts.reviewed_hierarchy(body, "alpha") is None
    body.tail = "External whitespace does not change the body"
    assert spl_layouts.reviewed_hierarchy(body, "fluconazole") == hierarchy
    body.append(ET.fromstring("<paragraph>New qualification.</paragraph>"))
    assert spl_layouts.reviewed_hierarchy(body, "fluconazole") is None


def test_table_cells_never_become_flattened_prose_support():
    from dataclasses import replace
    from test_passage_grounding import label
    from src.domain.explanation.passages import source_passages

    card = label("Reduce alpha to 25 mg.")
    card = replace(
        card,
        payload={
            "passages": [
                {
                    "section": "dose",
                    "text": "Reduce alpha to 25 mg.",
                    "section_complete": True,
                    "content_kind": "structured_table_section",
                }
            ]
        },
    )
    assert source_passages(card) == []


def test_retained_tables_do_not_displace_prose_selection_budget():
    from src.domain.evidence.label_passages import select_label_passages

    base = {
        "section_path": ["Context"],
        "parent_context": [],
        "boundary_policy": "whole",
    }
    label = {
        "query": "alpha",
        "sections": {},
        "source_units": [
            {
                **base,
                "section": "large_table",
                "text": "renal " * 1900,
                "content_kind": "structured_table_section",
            },
            {
                **base,
                "section": "warning",
                "text": "Alpha changes beta clearance. Only the reported study population was assessed.",
            },
        ],
    }
    selected = select_label_passages(
        label, ["alpha", "beta"], {"renal_function": "severe_impairment"}
    )
    assert (
        len(selected["passages"]) == 1
        and selected["passages"][0]["section"] == "warning"
    )
    assert len(selected["source_units"]) == 2
