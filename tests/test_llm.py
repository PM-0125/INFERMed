# tests/test_llm.py
import re
import types
import pytest

from src.llm.llm_interface import build_prompt, generate_response

# ---------- Minimal canonical context ----------
MINIMAL_CTX = {
    "drugs": {
        "a": {"name": "DrugA", "ids": {"pubchem_cid": None, "drugbank": None}},
        "b": {"name": "DrugB", "ids": {"pubchem_cid": None, "drugbank": None}},
    },
    "signals": {
        "mechanistic": {},
        "faers": {},
        "tabular": {},
    },
    "sources": {"duckdb": [], "qlever": [], "openfda": []},
    "caveats": [],
}


def test_template_fill_smoke_patient():
    """Prompt should include drug names and explicit 'no evidence' text."""
    p = build_prompt(MINIMAL_CTX, "Patient")
    assert "DrugA" in p and "DrugB" in p
    # From FAERS formatter
    assert "No evidence from FAERS" in p or "FAERS" in p


def test_missing_sections_do_not_invent_mechanisms():
    """With no mechanistic info, PD section should say none is found."""
    p = build_prompt(MINIMAL_CTX, "Doctor")
    assert "No common pathways found" in p
    assert "No overlapping targets found" in p
    # Make sure it didn't hallucinate targets
    assert "targets:" not in p.lower() or "(none)" in p.lower()


def test_build_prompt_is_deterministic_same_input():
    """Same context + mode => identical prompt text (pre-LLM)."""
    p1 = build_prompt(MINIMAL_CTX, "Pharma")
    p2 = build_prompt(MINIMAL_CTX, "Pharma")
    assert p1 == p2


def test_generate_response_error_path_disclaimer(monkeypatch):
    """
    Force a connection error; ensure fallback text appears and patient disclaimer is present.
    """
    # Create a fake requests module with a post() that raises
    import requests

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("nope")

    monkeypatch.setattr(requests, "post", boom, raising=True)

    out = generate_response(MINIMAL_CTX, "Patient", seed=123)
    assert isinstance(out, dict)
    assert "Unable to generate" in out["text"]
    assert "not medical advice" in out["text"].lower()
    assert out.get("meta", {}).get("error") is True


def test_truncation_notice_for_large_context():
    """When context is very large, a truncation notice should be appended."""
    huge_list = [f"path_{i}" for i in range(200)]
    big_ctx = {
        "drugs": {
            "a": {"name": "DrugA", "ids": {"pubchem_cid": "111", "drugbank": "DB00001"}},
            "b": {"name": "DrugB", "ids": {"pubchem_cid": "222", "drugbank": "DB00002"}},
        },
        "signals": {
            "mechanistic": {
                "enzymes": {
                    "a": {"substrate": ["CYP3A4"], "inhibitor": [], "inducer": []},
                    "b": {"substrate": [], "inhibitor": ["CYP3A4"], "inducer": []},
                },
                "targets_a": huge_list,
                "targets_b": huge_list,
                "pathways_a": huge_list,
                "pathways_b": huge_list,
                "common_pathways": huge_list,
            },
            "faers": {
                "top_reactions_a": [(f"AE_{i}", i) for i in range(50)],
                "top_reactions_b": [(f"BE_{i}", i) for i in range(50)],
                "combo_reactions": [(f"CE_{i}", i) for i in range(50)],
            },
            "tabular": {
                "prr": 2.5,
                "dili_a": "low",
                "dili_b": "medium",
                "dict_a": "mild",
                "dict_b": "severe",
                "diqt_a": 0.1,
                "diqt_b": 0.2,
            },
        },
        "sources": {"duckdb": ["TwoSides"], "qlever": ["PubChem RDF subset"], "openfda": ["FAERS"]},
        "caveats": ["QLever index sampled subset"],
    }

    p = build_prompt(big_ctx, "Doctor")
    assert "[Note: some context was truncated for length.]" in p

def test_manual_demo_print_only():
    from src.llm.llm_interface import generate_response
    ctx = {
        "drugs": {"a":{"name":"warfarin","ids":{}}, "b":{"name":"fluconazole","ids":{}}},
        "signals": {"mechanistic":{"enzymes":{
            "a":{"substrate":["CYP2C9"],"inhibitor":[],"inducer":[]},
            "b":{"substrate":[],"inhibitor":["CYP2C9"],"inducer":[]}
        }}},
        "sources": {"duckdb":["TwoSides"], "qlever":["PubChem RDF subset"], "openfda":["FAERS"]},
    }
    out = generate_response(ctx, "Doctor", seed=42)
    print(out["text"])

# --- Optional add-ons below your current tests ---

def test_history_block_is_included_in_prompt():
    hist = [
        {"role": "user", "text": "Are DrugA and DrugB safe together?"},
        {"role": "assistant", "text": "They may interact; monitor."},
        {"role": "user", "text": "What should I watch for?"}
    ]
    p = build_prompt(MINIMAL_CTX, "Patient", history=hist)
    assert "## HISTORY (previous turns, summarized)" in p
    # Most recent user line should appear
    assert "What should I watch for?" in p
    # Policy block should be present
    assert "[POLICY]" in p

def test_strip_template_safety_and_single_disclaimer(monkeypatch):
    """Simulate a model that echoes a template safety line; ensure we strip it and append exactly one disclaimer."""
    import requests

    class FakeResp:
        status_code = 200
        def json(self):
            return {
                "response": '...analysis...\n"This is research software; final decisions rest with licensed clinicians."\n',
                "eval_count": 10,
                "prompt_eval_count": 100,
            }

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(), raising=True)

    out = generate_response(MINIMAL_CTX, "Doctor", seed=1)
    # The template safety line should be gone
    assert '"This is research software' not in out["text"]
    # Our code-appended disclaimer should be present exactly once
    assert out["text"].count("Disclaimer: Research prototype.") == 1

def test_policy_present_in_prompt_patient_mode():
    p = build_prompt(MINIMAL_CTX, "Patient")
    assert "[POLICY]" in p
    assert "Use ONLY the evidence" in p




def test_sources_block_explicit_when_empty():
    """If sources dict is empty, the prompt should say '(none)' for each."""
    ctx = {**MINIMAL_CTX, "sources": {"duckdb": [], "qlever": [], "openfda": []}}
    p = build_prompt(ctx, "Pharma")
    assert "duckdb: (none)" in p.lower()
    assert "qlever: (none)" in p.lower()
    assert "openfda: (none)" in p.lower()
