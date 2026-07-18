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
    monkeypatch.setenv("INFERMED_DATA_MODE", "full_research_future")
    monkeypatch.setenv("ENABLE_QLEVER", "true")
    monkeypatch.setenv("ENABLE_OPENFDA_LABEL", "false")
    monkeypatch.setenv("ENABLE_DAILYMED", "false")
    monkeypatch.setenv("ENABLE_RXNORM", "false")
    monkeypatch.setenv("ENABLE_FDA_PGX", "false")
    monkeypatch.setenv("ENABLE_EUROPE_PMC", "false")
    monkeypatch.setenv("ENABLE_OPEN_TARGETS", "false")
    monkeypatch.setenv("ENABLE_STRINGDB", "false")
    monkeypatch.setenv("ENABLE_DRUGCENTRAL", "false")
    monkeypatch.setenv("ENABLE_BIOGRID", "false")
    monkeypatch.setattr(rp, "get_semantic_searcher", lambda: None, raising=True)
    monkeypatch.setattr(rp, "get_reranker", lambda: None, raising=True)

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

    from src.retrieval import chembl_client, kegg_client, pubchem_client, reactome_client, uniprot_client

    monkeypatch.setattr(pubchem_client, "get_compound_cid_by_name", lambda drug: None, raising=False)
    monkeypatch.setattr(pubchem_client, "get_compound_pk_data", lambda cid: {}, raising=True)
    monkeypatch.setattr(pubchem_client, "get_compound_pk_data_by_name", lambda drug: {}, raising=True)
    monkeypatch.setattr(kegg_client, "get_drug_pathways", lambda drug: [], raising=True)
    monkeypatch.setattr(kegg_client, "get_common_pathways", lambda a, b: [], raising=True)
    monkeypatch.setattr(kegg_client, "get_drug_enzymes", lambda drug: [], raising=True)
    monkeypatch.setattr(kegg_client, "get_metabolism_pathway", lambda drug: {"pathways": [], "enzymes": []}, raising=True)
    monkeypatch.setattr(reactome_client, "get_common_pathways_for_proteins", lambda ids: [], raising=True)
    monkeypatch.setattr(reactome_client, "get_drug_target_pathways", lambda drug, ids: [], raising=True)
    monkeypatch.setattr(uniprot_client, "enrich_protein_list", lambda seeds: [], raising=True)
    monkeypatch.setattr(
        chembl_client,
        "enrich_mechanistic_data",
        lambda drug, enzymes: {
            "enzymes": {},
            "enzyme_strength": {"strong": [], "moderate": [], "weak": []},
            "chembl_validation": {"found": False, "matches": [], "mismatches": []},
        },
        raising=True,
    )


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


def test_qlever_disabled_does_not_call_query_module(monkeypatch):
    monkeypatch.setenv("INFERMED_DATA_MODE", "public_safe")
    monkeypatch.setenv("ENABLE_QLEVER", "true")

    class BoomQL:
        def get_mechanistic_enriched(self, *_args, **_kwargs):
            raise AssertionError("QLever should not be called in public_safe mode")

    monkeypatch.setattr(rp, "ql", BoomQL(), raising=True)

    out = rp._get_qlever_mechanistic_or_stub("A", "B")

    assert out["targets_a"] == []
    assert any("disabled" in c.lower() for c in out["caveats"])


def test_public_safe_rest_enrichment_fills_qlever_gap(monkeypatch):
    monkeypatch.setenv("INFERMED_DATA_MODE", "public_safe")
    monkeypatch.setenv("ENABLE_QLEVER", "true")
    monkeypatch.setenv("ENABLE_OPENFDA", "false")
    monkeypatch.setenv("ENABLE_OPENFDA_LABEL", "false")
    monkeypatch.setenv("ENABLE_DAILYMED", "false")
    monkeypatch.setenv("ENABLE_RXNORM", "false")
    monkeypatch.setenv("ENABLE_PUBCHEM_REST", "true")
    monkeypatch.setenv("ENABLE_KEGG", "false")
    monkeypatch.setenv("ENABLE_CHEMBL", "false")
    monkeypatch.setenv("ENABLE_UNIPROT", "false")
    monkeypatch.setenv("ENABLE_REACTOME", "false")
    monkeypatch.setenv("ENABLE_FDA_PGX", "false")
    monkeypatch.setenv("ENABLE_EUROPE_PMC", "false")
    monkeypatch.setenv("ENABLE_OPEN_TARGETS", "false")
    monkeypatch.setenv("ENABLE_STRINGDB", "false")
    monkeypatch.setenv("ENABLE_DRUGCENTRAL", "false")
    monkeypatch.setenv("ENABLE_BIOGRID", "false")
    monkeypatch.setattr(rp, "get_semantic_searcher", lambda: None, raising=True)
    monkeypatch.setattr(rp, "get_reranker", lambda: None, raising=True)

    class FakeDuckDBClient:
        def __init__(self, *a, **k): pass
        def get_available_sources(self): return {}
        def get_interaction_score(self, a, b): return None
        def get_side_effects(self, *a, **k): return []
        def get_dilirank_score(self, d): return None
        def get_dictrank_score(self, d): return None
        def get_diqt_score(self, d): return None
        def get_drug_targets(self, d): return []
        def get_synonyms(self, d): return []

    class FakeDQModule(types.SimpleNamespace):
        def init_duckdb_connection(self, *a, **k): return None
        DuckDBClient = FakeDuckDBClient

    monkeypatch.setattr(rp, "dq", FakeDQModule(), raising=True)

    class BoomQL:
        def get_mechanistic_enriched(self, *_args, **_kwargs):
            raise AssertionError("QLever should not be called in public_safe mode")

    monkeypatch.setattr(rp, "ql", BoomQL(), raising=True)

    from src.retrieval import chembl_client, kegg_client, pubchem_client, reactome_client, uniprot_client

    monkeypatch.setattr(pubchem_client, "get_compound_cid_by_name", lambda drug: {"ADrug": "111", "BDrug": "222"}.get(drug), raising=True)
    monkeypatch.setattr(pubchem_client, "get_compound_pk_data", lambda cid: {"molecular_weight": 300.0, "log_p": 2.1}, raising=True)
    monkeypatch.setattr(
        kegg_client,
        "get_drug_pathways",
        lambda drug: [{"pathway_id": f"hsa-{drug}", "pathway_name": f"{drug} metabolism"}],
        raising=True,
    )
    monkeypatch.setattr(
        kegg_client,
        "get_common_pathways",
        lambda a, b: [{"pathway_id": "hsa-common", "pathway_name": "Shared metabolism"}],
        raising=True,
    )
    monkeypatch.setattr(
        kegg_client,
        "get_drug_enzymes",
        lambda drug: [{"enzyme_id": "1.14.14.1", "enzyme_name": "Cytochrome P450 3A4"}],
        raising=True,
    )
    monkeypatch.setattr(
        kegg_client,
        "get_metabolism_pathway",
        lambda drug: {"pathways": [], "enzymes": [{"enzyme_id": "1.14.14.1", "enzyme_name": "Cytochrome P450 3A4"}]},
        raising=True,
    )
    monkeypatch.setattr(
        chembl_client,
        "enrich_mechanistic_data",
        lambda drug, enzymes: {
            "enzymes": enzymes,
            "enzyme_strength": {"strong": ["cyp3a4"], "moderate": [], "weak": []},
            "chembl_validation": {"found": True, "matches": [], "mismatches": ["cyp3a4"]},
        },
        raising=True,
    )
    monkeypatch.setattr(
        uniprot_client,
        "enrich_protein_list",
        lambda seeds: [
            {
                "original_id": seeds[0],
                "uniprot_id": "P08684",
                "name": "Cytochrome P450 3A4",
                "gene_names": ["CYP3A4"],
            }
        ] if seeds else [],
        raising=True,
    )
    monkeypatch.setattr(
        reactome_client,
        "get_drug_target_pathways",
        lambda drug, ids: [{"pathway_id": f"R-HSA-{drug}", "pathway_name": f"{drug} Reactome pathway"}],
        raising=True,
    )
    monkeypatch.setattr(reactome_client, "get_common_pathways_for_proteins", lambda ids: [], raising=True)

    ctx = rp.retrieve_and_normalize("ADrug", "BDrug", parquet_dir="/dev/null", openfda_cache="/dev/null")
    mech = ctx["signals"]["mechanistic"]

    assert ctx["drugs"]["a"]["ids"]["pubchem_cid"] == "111"
    assert ctx["drugs"]["b"]["ids"]["pubchem_cid"] == "222"
    assert mech["pk_data_a"]["molecular_weight"] == 300.0
    assert mech["kegg_pathways_a"][0]["pathway_id"] == "hsa-ADrug"
    assert mech["kegg_common_pathways"][0]["pathway_id"] == "hsa-common"
    assert mech["kegg_enzymes_a"][0]["enzyme_id"] == "1.14.14.1"
    assert mech["chembl_enrichment"]["a"]["chembl_validation"]["found"] is True
    assert set(mech["chembl_enrichment"]) == {"a", "b"}
    assert mech["uniprot_ids_a"] == ["P08684"]
    assert mech["uniprot_targets_a"][0]["gene_names"] == ["CYP3A4"]
    assert mech["reactome_pathways_a"][0]["pathway_id"] == "R-HSA-ADrug"
    assert "cyp3a4" in mech["enzymes"]["a"]["substrate"]
    assert "cyp3a4" in mech["enzymes"]["a"]["inhibitor"]
    for key in (
        "ids_a",
        "ids_b",
        "pk_data_a",
        "pk_data_b",
        "uniprot_ids_a",
        "uniprot_ids_b",
        "uniprot_targets_a",
        "uniprot_targets_b",
        "kegg_pathways_a",
        "kegg_pathways_b",
        "kegg_common_pathways",
        "kegg_enzymes_a",
        "kegg_enzymes_b",
        "reactome_pathways_a",
        "reactome_pathways_b",
        "chembl_enrichment",
    ):
        assert key in mech
    assert ctx["sources"]["qlever"] == []
    assert "PubChem REST API" in ctx["sources"]["apis"]
    assert "KEGG REST API" in ctx["sources"]["apis"]
    assert "UniProt REST API" in ctx["sources"]["apis"]
    assert "Reactome REST API" in ctx["sources"]["apis"]
    assert "ChEMBL REST API" in ctx["sources"]["apis"]


def test_clinical_reference_context_is_attached(monkeypatch):
    _monkeypatch_retrievals(monkeypatch)
    monkeypatch.setenv("ENABLE_OPENFDA_LABEL", "true")
    monkeypatch.setenv("ENABLE_DAILYMED", "true")
    monkeypatch.setenv("ENABLE_RXNORM", "true")

    def fake_clinical_reference(a, b, settings, caveats):
        return {
            "rxnorm": {
                "a": {"resolved": True, "rxcui": "1111", "name": a, "classes": [{"class_name": "Anticoagulants"}]},
                "b": {"resolved": True, "rxcui": "2222", "name": b, "classes": []},
            },
            "openfda_label": {
                "a": {"found": True, "effective_time": "20240101", "sections": {"drug_interactions": "Interaction section"}},
                "b": {"found": False, "sections": {}},
            },
            "dailymed": {
                "a": {"found": True, "records": [{"title": "A SPL", "set_id": "set-a"}]},
                "b": {"found": False, "records": []},
            },
            "fda_ddi_reference": {
                "a": {"matches": [{"row": {"Enzyme": "CYP2C9", "Substrate": a}}]},
                "b": {"matches": []},
            },
        }

    monkeypatch.setattr(rp, "_collect_public_clinical_reference", fake_clinical_reference, raising=True)

    ctx = rp.retrieve_and_normalize("ADrug", "BDrug")

    clinical = ctx["signals"]["clinical_reference"]
    assert clinical["rxnorm"]["a"]["rxcui"] == "1111"
    assert ctx["drugs"]["a"]["ids"]["rxcui"] == "1111"
    assert ctx["meta"]["clinical_reference_contributed"] is True
    assert "openFDA Drug Label API" in ctx["sources"]["apis"]
    assert "DailyMed SPL API" in ctx["sources"]["apis"]
    assert "RxNorm/RxClass API" in ctx["sources"]["apis"]
    assert "FDA CYP/transporter reference" in ctx["sources"]["apis"]


def test_research_enrichment_context_is_attached(monkeypatch):
    _monkeypatch_retrievals(monkeypatch)

    def fake_research_enrichment(a, b, mech, settings, caveats):
        return {
            "europe_pmc": {
                "found": True,
                "articles": [{"title": f"{a} {b} DDI review", "year": "2026", "pmid": "1"}],
            },
            "fda_pgx": {"found": False, "a": [], "b": []},
            "open_targets": {},
            "stringdb": {},
            "biogrid": {},
        }

    monkeypatch.setattr(rp, "_collect_api_research_enrichment", fake_research_enrichment, raising=True)

    ctx = rp.retrieve_and_normalize("ADrug", "BDrug")

    research = ctx["signals"]["research_enrichment"]
    assert research["europe_pmc"]["articles"][0]["title"] == "ADrug BDrug DDI review"
    assert ctx["meta"]["research_enrichment_contributed"] is True


def test_context_cache_key_is_unordered(monkeypatch, tmp_path):
    _monkeypatch_retrievals(monkeypatch)
    # Use temp cache dirs already wired in fixture
    c1, k1 = rp.get_context_cached("A", "B")
    c2, k2 = rp.get_context_cached("B", "A")
    assert k1 == k2
    assert k1 == "a_b"
    # Should load from cache on second call
    assert c1 == c2


def test_run_rag_uses_llm_and_history(monkeypatch):
    _monkeypatch_retrievals(monkeypatch)

    # stub the LLM call inside rag_pipeline namespace
    def fake_generate_response(context, mode, seed=None, temperature=0.2, history=None, **kw):
        assert history == [{"role": "user", "text": "prev"}]  # passthrough check
        assert kw.get("stream") is False
        return {"text": f"OK-{mode}", "usage": {}, "meta": {"model": "fake"}}

    monkeypatch.setattr(rp, "generate_response", fake_generate_response, raising=True)

    out = rp.run_rag("A", "B", mode="Doctor", history=[{"role": "user", "text": "prev"}], stream=False)
    assert out["answer"]["text"] == "OK-Doctor"
    assert "signals" in out["context"]  # context was built


def test_run_rag_does_not_force_ollama_model_override(monkeypatch):
    _monkeypatch_retrievals(monkeypatch)
    monkeypatch.setattr(rp, "LLM_MODEL_NAME", "gpt-oss", raising=True)

    seen = {}

    def fake_generate_response(context, mode, seed=None, temperature=0.2, history=None, **kw):
        seen.update(kw)
        return {"text": "OK", "usage": {}, "meta": {"model": "fake"}}

    monkeypatch.setattr(rp, "generate_response", fake_generate_response, raising=True)

    rp.run_rag("A", "B", mode="Doctor")

    assert seen["model_name"] is None
