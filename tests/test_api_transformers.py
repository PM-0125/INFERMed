from src.api.transformers import (
    build_interaction_result,
    build_openfda_card,
    calculate_risk_summary,
    parse_assessment_sections,
)


def _context():
    return {
        "drugs": {
            "a": {"name": "warfarin", "ids": {"pubchem_cid": 54678486}, "synonyms": ["Coumadin"]},
            "b": {"name": "fluconazole", "ids": {"pubchem_cid": 3365}},
        },
        "signals": {
            "tabular": {
                "prr": 2.4,
                "side_effects_a": ["bleeding", "bruising"],
                "side_effects_b": ["nausea"],
                "dili_a": 0.1,
                "dili_b": 0.3,
                "dict_a": "unknown",
                "dict_b": "unknown",
                "diqt_a": None,
                "diqt_b": None,
            },
            "faers": {
                "top_reactions_a": [["bleeding", 12]],
                "top_reactions_b": [["nausea", 5]],
                "combo_reactions": [["international normalised ratio increased", 3]],
            },
            "mechanistic": {
                "enzymes": {"a": {"substrate": ["cyp2c9"]}, "b": {"inhibitor": ["cyp2c9"]}},
                "targets_a": ["target-a"],
                "targets_b": ["target-b"],
                "common_pathways": ["coagulation"],
                "uniprot_ids_a": ["P11712"],
                "kegg_pathways_a": [{"pathway_id": "hsa00982", "pathway_name": "Drug metabolism - cytochrome P450"}],
                "reactome_pathways_b": [{"pathway_id": "R-HSA-1234", "pathway_name": "Hemostasis"}],
                "chembl_enrichment": {
                    "a": {
                        "chembl_validation": {"found": True, "matches": ["cyp2c9"], "mismatches": []},
                        "enzyme_strength": {"strong": ["cyp2c9"], "moderate": [], "weak": []},
                    }
                },
            },
        },
        "pkpd": {
            "pk_summary": "Potential increased exposure via inhibition at cyp2c9",
            "pd_summary": "Common pathways: coagulation",
            "pk_detail": {"overlaps": {"inhibition": ["cyp2c9"], "induction": [], "shared_substrate": []}},
            "pd_detail": {"overlap_targets": [], "overlap_pathways": ["coagulation"]},
        },
        "sources": {"duckdb": ["TwoSides"], "openfda": ["FAERS via OpenFDA (cached)"], "qlever": []},
        "source_status": [{"name": "OpenFDA cache/API", "enabled": True, "available": True, "reason": "ok"}],
        "meta": {"data_mode": "public_safe", "created_at": "2026-05-16T08:00:00Z"},
    }


def test_parse_assessment_sections_markdown():
    sections = parse_assessment_sections("## Bottom Line\nUse caution.\n\n## Evidence Limitations\nFAERS is associative.")
    assert sections == [
        {"title": "Bottom Line", "body": "Use caution."},
        {"title": "Evidence Limitations", "body": "FAERS is associative."},
    ]


def test_parse_assessment_sections_fallback():
    assert parse_assessment_sections("Plain answer") == [{"title": "Assessment", "body": "Plain answer"}]


def test_calculate_risk_unknown_when_no_evidence():
    level, label, confidence = calculate_risk_summary({"signals": {}, "pkpd": {}})
    assert level == "unknown"
    assert label == "Insufficient Evidence"
    assert "No usable evidence" in confidence


def test_build_interaction_result_contract():
    result = build_interaction_result(
        {"context": _context(), "answer": {"text": "## Bottom Line\nMonitor closely."}},
        fallback_drug_a="warfarin",
        fallback_drug_b="fluconazole",
    )
    assert result["risk"]["level"] == "high"
    assert result["drugs"][0]["pubchemCid"] == "54678486"
    assert result["assessment"][0]["title"] == "Bottom Line"
    assert "openfda" in result["evidence"]
    assert result["evidence"]["openfda"]["caveat"]
    mechanism_rows = result["evidence"]["mechanisms"]["rows"]
    assert any(row["meta"] == "UniProt" for row in mechanism_rows)
    assert any(row["meta"] == "KEGG" for row in mechanism_rows)
    assert any(row["meta"] == "Reactome" for row in mechanism_rows)
    assert any(row["meta"] == "ChEMBL" for row in mechanism_rows)


def test_openfda_card_labels_displayed_subset_counts():
    card = build_openfda_card(
        {
            "top_reactions_a": [[f"a{i}", i] for i in range(15)],
            "top_reactions_b": [[f"b{i}", i] for i in range(3)],
            "combo_reactions": [[f"c{i}", i] for i in range(12)],
        }
    )

    metrics = {item["label"]: item["value"] for item in card["metrics"]}
    assert metrics["Drug A rows"] == "top 10 of 15"
    assert metrics["Drug B rows"] == "3"
    assert metrics["Combo rows"] == "top 10 of 12"
    assert "top 10 of 15 for Drug A" in card["caveat"]
    assert "top 10 of 12 for combination" in card["caveat"]
    assert len(card["rows"]) == 23
    assert card["rows"][0]["meta"] == "n=0"
