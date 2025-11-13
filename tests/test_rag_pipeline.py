# tests/test_rag_pipeline.py
import json
import os
import shutil
import types
import pytest

# module under test
from src.llm import rag_pipeline as rp


@pytest.fixture(autouse=True)
def clean_cache(tmp_path, monkeypatch):
    # Redirect cache dirs to a temp folder so we don't touch real repo cache
    cache_dir = tmp_path / "cache"
    ctx_dir = cache_dir / "contexts"
    resp_dir = cache_dir / "responses"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    resp_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(rp, "CACHE_DIR", str(cache_dir), raising=True)
    monkeypatch.setattr(rp, "CTX_DIR", str(ctx_dir), raising=True)
    monkeypatch.setattr(rp, "RESP_DIR", str(resp_dir), raising=True)

    yield
    shutil.rmtree(cache_dir, ignore_errors=True)


def _monkeypatch_retrievals(monkeypatch,
                            duck_targets_a=None,
                            duck_targets_b=None,
                            qlever_enriched=None,
                            faers_a=None,
                            faers_b=None,
                            faers_combo=None):
    # ---- DuckDB stubs ----
    class FakeDuckDBClient:
        def __init__(self, *a, **k): pass
        def get_interaction_score(self, a, b): return 2.5
        def get_dili_risk(self, d): return "low" if d.lower().startswith("a") else "medium"
        def get_dict_rank(self, d): return "mild"
        def get_diqt_score(self, d): return 0.1
        def get_side_effects(self, d, drug_b=None, top_k=20, min_prr=1.0):
            if drug_b is not None:
                # Pair query
                return ["Bleeding", "Bruising"]
            return ["Headache", "Nausea", "Rash"]
        def get_drug_targets(self, d):
            if isinstance(d, list):
                # Batch mode
                return {drug: (duck_targets_a if drug.lower().startswith("a") else duck_targets_b) or ["HMGCR"] for drug in d}
            if d.lower().startswith("a"):
                return duck_targets_a or ["HMGCR", "PCSK9"]
            return duck_targets_b or ["EGFR", "BRAF"]
        def get_dilirank_score(self, d): return 0.3 if d.lower().startswith("a") else 0.5
        def get_dictrank_score(self, d): return 0.2
        def get_synonyms(self, d): return [d]

    class FakeDQModule(types.SimpleNamespace):
        def init_duckdb_connection(self, *a, **k): return None
        DuckDBClient = FakeDuckDBClient

    fake_dq = FakeDQModule()
    monkeypatch.setattr(rp, "dq", fake_dq, raising=True)

    # ---- OpenFDA stubs ----
    class FakeOpenFDA:
        def __init__(self, cache_dir=None): pass
        def get_top_reactions(self, d, top_k=10): 
            return (faers_a if d.lower().startswith("a") else faers_b) or [("Headache", 10), ("Rash", 5)]
        def get_combination_reactions(self, a, b, top_k=10):
            return faers_combo or [("Bleeding", 2)]

    monkeypatch.setattr(rp, "OpenFDAClient", FakeOpenFDA, raising=True)

    # ---- QLever stubs ----
    class FakeQL(types.SimpleNamespace):
        def get_mechanistic_enriched(self, A, B):
            if isinstance(qlever_enriched, Exception):
                raise qlever_enriched
            if qlever_enriched is None:
                # default enriched with dict-shaped targets and simple enzyme roles
                return {
                    "enzymes": {
                        "a": {"substrate": ["CYP3A4"], "inhibitor": [], "inducer": []},
                        "b": {"substrate": [], "inhibitor": ["CYP3A4"], "inducer": []},
                    },
                    "targets_a": [{"label": "HMGCR"}, {"label": "PCSK9"}],
                    "targets_b": [{"label": "EGFR"}, {"uri": "http://no-label"}],
                    "pathways_a": [{"label": "PathA"}],
                    "pathways_b": [{"label": "PathB"}],
                    "common_pathways": [{"label": "CommonP"}],
                    "ids_a": {"pubchem_cid": "111"},
                    "ids_b": {"pubchem_cid": "222"},
                    "synonyms_a": ["Alpha"],
                    "synonyms_b": ["Beta"],
                    "caveats": [],
                }
            return qlever_enriched

        def get_mechanistic(self, A, B):
            # basic fallback
            return {
                "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                            "b": {"substrate": [], "inhibitor": [], "inducer": []}},
                "targets_a": [],
                "targets_b": [],
                "pathways_a": [],
                "pathways_b": [],
                "common_pathways": [],
                "ids_a": {},
                "ids_b": {},
                "synonyms_a": [],
                "synonyms_b": [],
                "caveats": ["basic stub"],
            }

    fake_ql = FakeQL()
    monkeypatch.setattr(rp, "ql", fake_ql, raising=True)


def test_retrieve_and_normalize_enriched(monkeypatch):
    _monkeypatch_retrievals(monkeypatch)

    ctx = rp.retrieve_and_normalize("ADrug", "BDrug",
                                    parquet_dir="/dev/null",
                                    openfda_cache="/dev/null",
                                    topk_side_effects=2,
                                    topk_faers=1,
                                    topk_targets=2,
                                    topk_pathways=1)
    # basic shape
    assert set(ctx.keys()) >= {"drugs", "signals", "sources", "caveats", "pkpd", "meta"}
    mech = ctx["signals"]["mechanistic"]
    # dict-shaped targets normalized to strings, trimmed
    assert mech["targets_a"] == ["hmgcr", "pcsk9"]
    # Check that targets_b contains egfr and the URI (normalized)
    targets_b_lower = [t.lower() for t in mech["targets_b"]]
    assert "egfr" in targets_b_lower
    # URI may be normalized (hyphens/spaces), so check for partial match
    assert any("no" in t.lower() and "label" in t.lower() for t in mech["targets_b"])
    assert mech["pathways_a"] == ["patha"]
    assert mech["common_pathways"] == ["commonp"]
    # pkpd present and sensible
    assert "pk_summary" in ctx["pkpd"] and "pd_summary" in ctx["pkpd"]
    # sources reflect qlever
    assert "qlever" in ctx["sources"]
    # FAERS trimmed
    fa = ctx["signals"]["faers"]
    assert len(fa["top_reactions_a"]) == 1
    assert len(fa["top_reactions_b"]) == 1
    # meta contains pair_key and version
    assert "pair_key" in ctx["meta"] and "version" in ctx["meta"]


def test_retrieve_and_normalize_qlever_stub_fallback(monkeypatch):
    # make enriched raise to trigger basic->stub path
    _monkeypatch_retrievals(monkeypatch, qlever_enriched=RuntimeError("boom"))
    ctx = rp.retrieve_and_normalize("ADrug", "BDrug")
    mech = ctx["signals"]["mechanistic"]
    # Since QLever failed, PD targets should come from DuckDB fallback (normalized)
    assert mech["targets_a"] == ["hmgcr", "pcsk9"]
    # caveats should indicate QLever fallback somewhere
    assert any("fallback" in c.lower() or "unavailable" in c.lower() for c in ctx["caveats"])


def test_context_cache_key_is_unordered(monkeypatch, tmp_path):
    _monkeypatch_retrievals(monkeypatch)
    # Use temp cache dirs already wired in fixture
    c1, k1 = rp.get_context_cached("A", "B")
    c2, k2 = rp.get_context_cached("B", "A")
    assert k1 == k2
    # Should load from cache on second call
    assert c1 == c2


def test_run_rag_uses_llm_and_history(monkeypatch):
    _monkeypatch_retrievals(monkeypatch)

    # stub the LLM call inside rag_pipeline namespace
    def fake_generate_response(context, mode, seed=None, temperature=0.2, history=None, **kw):
        assert history == [{"role": "user", "text": "prev"}]  # passthrough check
        return {"text": f"OK-{mode}", "usage": {}, "meta": {"model": "fake"}}

    monkeypatch.setattr(rp, "generate_response", fake_generate_response, raising=True)

    out = rp.run_rag("A", "B", mode="Doctor", history=[{"role": "user", "text": "prev"}])
    assert out["answer"]["text"] == "OK-Doctor"
    assert "signals" in out["context"]  # context was built
