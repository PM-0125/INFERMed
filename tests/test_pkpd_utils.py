# tests/test_pkpd_utils.py
import math
import pytest

from src.utils.pkpd_utils import (
    canonicalize_enzyme,
    canonicalize_list,
    extract_pk_roles,
    detect_pk_overlaps,
    pd_overlap,
    summarize_pkpd_risk,
    topk_faers,
    synthesize_mechanistic,
)

# ---------------------- normalization ----------------------

def test_canonicalize_enzyme_synonyms():
    assert canonicalize_enzyme("CYP3A4") == "cyp3a4"
    assert canonicalize_enzyme("Cytochrome P450 3A4") == "cyp3a4"
    assert canonicalize_enzyme(" cyp 3a4 ") == "cyp3a4"
    # unknown enzyme -> normalized lower token returned
    assert canonicalize_enzyme("Weird Enzyme X") == "weird enzyme x"


def test_canonicalize_list_dedup_and_topk():
    vals = ["A", "  a ", "B", "b", "C", "c", "D"]
    out = canonicalize_list(vals, topk=3)
    assert out == ["a", "b", "c"]  # dedup + normalized + topk kept in first-seen order


# ---------------------- PK roles & overlaps ----------------------

def test_extract_roles_and_overlap_detection():
    mech = {
        "enzymes": {
            "a": {"substrate": ["CYP2C9"], "inhibitor": [], "inducer": []},
            "b": {"substrate": [], "inhibitor": ["Cytochrome P450 2C9"], "inducer": []},
        }
    }
    roles = extract_pk_roles(mech)
    assert "cyp2c9" in roles["a"]["substrate"]
    assert "cyp2c9" in roles["b"]["inhibitor"]

    overlaps = detect_pk_overlaps(roles)
    assert "cyp2c9" in overlaps["inhibition"]  # A_sub & B_inh

    # add an inducer and shared substrate to test other cases
    mech["enzymes"]["a"]["inducer"] = ["cyp3a4"]
    mech["enzymes"]["b"]["substrate"] = ["CYP3A4"]
    mech["enzymes"]["a"]["substrate"].append("CYP3A4")
    roles2 = extract_pk_roles(mech)
    overlaps2 = detect_pk_overlaps(roles2)
    assert "cyp3a4" in overlaps2["induction"]      # B_sub & A_ind
    assert "cyp3a4" in overlaps2["shared_substrate"]  # both substrates


# ---------------------- PD overlap ----------------------

def test_pd_overlap_scores_and_lists():
    mech = {
        "targets_a": ["Target1", "Target2", "TargetX"],
        "targets_b": ["target1", "target2", "other"],
        "pathways_a": ["PathA", "PathB"],
        "pathways_b": ["pathA", "zzz"],
    }
    out = pd_overlap(mech)
    # overlaps should be normalized and sorted
    assert out["overlap_targets"] == ["target1", "target2"]
    assert out["overlap_pathways"] == ["patha"]
    # score is bounded [0,1] and > 0 with overlaps
    assert 0 < out["pd_score"] <= 1.0


# ---------------------- summaries ----------------------

def test_summarize_pkpd_risk_messages():
    mech = {
        "enzymes": {
            "a": {"substrate": ["CYP2D6"], "inhibitor": [], "inducer": []},
            "b": {"substrate": [], "inhibitor": ["CYP2D6"], "inducer": []},
        },
        "targets_a": ["H1 receptor", "COX-1"],
        "targets_b": ["cox-1", "5-HT2A"],
        "pathways_a": [],
        "pathways_b": [],
    }
    s = summarize_pkpd_risk("DrugA", "DrugB", mech)
    # PK summary should mention inhibition
    assert "inhibition" in s["pk_summary"].lower()
    # PD summary should mention overlapping targets (cox-1)
    assert "overlapping targets" in s["pd_summary"].lower()
    assert "cox-1" in s["pd_summary"].lower()
    # details present
    assert "roles" in s["pk_detail"] and "overlaps" in s["pk_detail"]
    assert "overlap_targets" in s["pd_detail"]


# ---------------------- FAERS formatting ----------------------

def test_topk_faers_basic_and_empty_and_malformed():
    faers = {
        "top_reactions_a": [("Headache", 10), ("Nausea", 5), ("Rash", 3)],
        "top_reactions_b": [],
        "combo_reactions": [("Bleeding", 2), ("Dizziness", 1), ("MalformedRow", "x"), ("OK", 0)],
    }
    out = topk_faers(faers, k=2)
    assert out["a"] == "Headache (n=10), Nausea (n=5)"
    assert out["b"] == "No evidence from FAERS."
    # malformed row should be skipped, still include valid ones up to k
    assert "Bleeding (n=2)" in out["combo"]
    assert "Dizziness (n=1)" in out["combo"]
    assert "MalformedRow" not in out["combo"]


# ---------------------- mechanistic merge ----------------------

def test_synthesize_mechanistic_with_fallback_targets():
    qlever_mech = {
        "enzymes": {"a": {"substrate": ["CYP3A4"], "inhibitor": [], "inducer": []},
                    "b": {"substrate": [], "inhibitor": [], "inducer": []}},
        "targets_a": [],  # empty -> should use fallback
        "targets_b": ["EGFR"],  # present -> should be kept
        "pathways_a": [],
        "pathways_b": [],
        "common_pathways": [],
    }
    fallback_a = ["HMGCR", "PCSK9"]
    fallback_b = ["X", "Y"]  # should be ignored because targets_b already present
    mech = synthesize_mechanistic(qlever_mech, fallback_targets_a=fallback_a, fallback_targets_b=fallback_b)

    assert mech["enzymes"]["a"]["substrate"] == ["cyp3a4"] or "cyp3a4" in mech["enzymes"]["a"]["substrate"]
    assert mech["targets_a"] == ["hmgcr", "pcsk9"]  # fallback applied & normalized
    assert mech["targets_b"] == ["egfr"]            # kept from qlever
    assert mech["pathways_a"] == []
    assert mech["common_pathways"] == []
