# tests/test_llm.py
import re
import types
import pytest

from src.llm.llm_interface import (
    build_prompt,
    generate_response,
    _collect_openai_stream,
    _nvidia_chat_completions_url,
)

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
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
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


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://integrate.api.nvidia.com", "https://integrate.api.nvidia.com/v1/chat/completions"),
        ("https://integrate.api.nvidia.com/v1", "https://integrate.api.nvidia.com/v1/chat/completions"),
        ("https://integrate.api.nvidia.com/v1/", "https://integrate.api.nvidia.com/v1/chat/completions"),
        (
            "https://integrate.api.nvidia.com/v1/chat/completions",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
    ],
)
def test_nvidia_endpoint_normalization(base_url, expected):
    assert _nvidia_chat_completions_url(base_url) == expected


def test_nvidia_404_error_explains_base_url(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("NVIDIA_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com")

    import requests

    seen = {}

    class FakeResp:
        status_code = 404
        text = "404 page not found"

        def json(self):
            raise ValueError("not json")

    def fake_post(url, **kwargs):
        seen["url"] = url
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post, raising=True)

    out = generate_response(MINIMAL_CTX, "Doctor", seed=123)

    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert "NVIDIA provider error 404" in out["text"]
    assert "model access" in out["text"]
    assert "check_nvidia.py --stream" in out["text"]


def test_nvidia_reasoning_effort_is_optional_payload_field(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("NVIDIA_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setenv("NVIDIA_REASONING_EFFORT", "low")
    monkeypatch.setenv("LLM_STREAM", "false")

    import requests

    seen = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": 1}}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs["json"]
        seen["headers"] = kwargs["headers"]
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post, raising=True)

    out = generate_response(MINIMAL_CTX, "Doctor", seed=123)

    assert seen["json"]["reasoning_effort"] == "low"
    assert seen["headers"]["Accept"] == "application/json"
    assert "OK" in out["text"]


def test_nvidia_uses_dedicated_timeout_and_non_stream_default(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("NVIDIA_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_TIMEOUT_S", "123")
    monkeypatch.delenv("LLM_STREAM", raising=False)
    monkeypatch.delenv("NVIDIA_STREAM", raising=False)

    import requests

    seen = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}], "usage": {}}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post, raising=True)

    out = generate_response(MINIMAL_CTX, "Doctor", seed=123)

    assert seen["json"]["stream"] is False
    assert seen["stream"] is False
    assert seen["timeout"] == 123.0
    assert seen["headers"]["Accept"] == "application/json"
    assert "OK" in out["text"]


def test_nvidia_stream_override_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("NVIDIA_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_STREAM", "true")

    import requests

    seen = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}], "usage": {}}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return FakeResp()

    monkeypatch.setattr(requests, "post", fake_post, raising=True)

    out = generate_response(MINIMAL_CTX, "Doctor", stream=False)

    assert seen["json"]["stream"] is False
    assert seen["stream"] is False
    assert seen["headers"]["Accept"] == "application/json"
    assert "OK" in out["text"]


def test_nvidia_nonstream_uses_reasoning_when_content_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("NVIDIA_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_STREAM", "false")

    import requests

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {"message": {"content": "", "reasoning_content": "hidden reasoning only"}}
                ],
                "usage": {"completion_tokens": 128},
            }

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResp(), raising=True)

    out = generate_response(MINIMAL_CTX, "Doctor", seed=123)

    assert out["meta"]["provider"] == "nvidia"
    assert "hidden reasoning only" in out["text"]


def test_nvidia_stream_collects_reasoning_content_when_content_missing():
    class FakeResp:
        def iter_lines(self, decode_unicode=True):
            yield 'data: {"choices":[{"delta":{"reasoning_content":"reasoning "}}]}'
            yield 'data: {"choices":[{"delta":{"reasoning_content":"only"}}]}'
            yield "data: [DONE]"

    assert _collect_openai_stream(FakeResp()) == "reasoning only"


def test_nvidia_stream_prefers_visible_content_over_reasoning():
    class FakeResp:
        def iter_lines(self, decode_unicode=True):
            yield 'data: {"choices":[{"delta":{"reasoning_content":"internal"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"visible"}}]}'
            yield "data: [DONE]"

    assert _collect_openai_stream(FakeResp()) == "visible"


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
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
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


def test_mock_provider_has_no_network_dependency(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    out = generate_response(MINIMAL_CTX, "Doctor", seed=1)

    assert out["meta"]["provider"] == "mock"
    assert "Bottom-line risk" in out["text"]
    assert "Disclaimer: Research prototype." in out["text"]

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


def test_prompt_includes_enrichment_sources_when_present():
    ctx = {
        **MINIMAL_CTX,
        "signals": {
            **MINIMAL_CTX["signals"],
            "mechanistic": {
                "targets_a": ["CYP2C9"],
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
        "sources": {
            "duckdb": ["TwoSides"],
            "qlever": [],
            "openfda": ["FAERS"],
            "apis": ["UniProt REST API", "KEGG REST API", "Reactome REST API"],
        },
    }
    p = build_prompt(ctx, "Doctor")
    assert "Returned enrichment sources: UniProt, KEGG, Reactome, ChEMBL" in p
    assert "UniProt A accessions: P11712" in p
    assert "KEGG A pathways: hsa00982: Drug metabolism - cytochrome P450" in p
    assert "Reactome A pathways" not in p
    assert "Reactome B pathways: R-HSA-1234: Hemostasis" in p
    assert "apis: UniProt REST API, KEGG REST API, Reactome REST API" in p
