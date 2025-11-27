# src/llm/rag_pipeline.py
from __future__ import annotations

import os
import json
import hashlib
from typing import Any, Dict, Tuple, Optional, List
from datetime import datetime
from collections.abc import Mapping  # <-- for _jsonify_sets

# Retrieval modules
from src.retrieval import duckdb_query as dq
from src.retrieval.openfda_api import OpenFDAClient
from src.retrieval import qlever_query as ql  # may expose get_mechanistic_enriched / get_mechanistic
from src.retrieval.semantic_search import get_semantic_searcher, SemanticSearcher

# PK/PD synthesis utilities
from src.utils.pkpd_utils import (
    synthesize_mechanistic,
    summarize_pkpd_risk,
)
from src.utils.relevance_scoring import (
    score_and_rank_side_effects,
    score_and_rank_pathways,
    score_and_rank_targets,
    merge_and_rerank_evidence,
)

# LLM interface (import model name so response cache keys are stable)
from src.llm.llm_interface import generate_response, MODEL_NAME as LLM_MODEL_NAME

# ----------------- versioning & caching -----------------
VERSION = 5  # bump when schema/logic changes to invalidate old cached contexts
# Version 5: Added semantic search with embeddings and relevance scoring/ranking
# Version 4: Added KEGG/Reactome/UniProt API integrations, enhanced PK/PD summarization,
#            PubChem PK data, target enrichment, pathway enhancements

CACHE_DIR = os.path.join("data", "cache")
CTX_DIR   = os.path.join(CACHE_DIR, "contexts")
RESP_DIR  = os.path.join(CACHE_DIR, "responses")
os.makedirs(CTX_DIR, exist_ok=True)
os.makedirs(RESP_DIR, exist_ok=True)


def _sha(obj: Any) -> str:
    s = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _ctx_path(key: str) -> str:
    return os.path.join(CTX_DIR, f"{key}.json")


def _resp_path(key: str) -> str:
    return os.path.join(RESP_DIR, f"{key}.json")


def _pair_key(drugA: str, drugB: str) -> str:
    """Stable cache key for unordered pairs: A+B == B+A."""
    a, b = drugA.strip().lower(), drugB.strip().lower()
    return f"{min(a,b)}|{max(a,b)}"


def _jsonify_sets(obj):
    """Recursively convert sets to sorted lists so json.dump succeeds."""
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, list):
        return [_jsonify_sets(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_jsonify_sets(x) for x in obj)
    if isinstance(obj, Mapping):
        return {k: _jsonify_sets(v) for k, v in obj.items()}
    return obj


def _to_pairs_list(x):
    """Normalize a list of (term, count) tuples to JSON-stable [[term, count], ...]."""
    out = []
    for t in (x or []):
        if isinstance(t, (list, tuple)) and len(t) == 2:
            out.append([t[0], t[1]])
    return out


# ----------------- retrieval helpers -----------------
def _get_qlever_mechanistic_or_stub(drugA: str, drugB: str) -> Dict[str, Any]:
    """
    Prefer ql.get_mechanistic_enriched(), fall back to ql.get_mechanistic().
    On error, return a minimal stub. synthesize_mechanistic() will add PD fallbacks.
    """
    # Try enriched first (if available)
    try:
        if hasattr(ql, "get_mechanistic_enriched"):
            return ql.get_mechanistic_enriched(drugA, drugB)
    except Exception:
        pass

    # Then try basic
    try:
        if hasattr(ql, "get_mechanistic"):
            return ql.get_mechanistic(drugA, drugB)
    except Exception:
        pass

    # Finally stub
    return {
        "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                    "b": {"substrate": [], "inhibitor": [], "inducer": []}},
        "targets_a": [], "targets_b": [],
        "pathways_a": [], "pathways_b": [],
        "common_pathways": [],
        "ids_a": {}, "ids_b": {},
        "synonyms_a": [], "synonyms_b": [],
        "caveats": ["QLever mechanistic unavailable; using DuckDB DrugBank targets as PD fallback."],
    }


def _bool_qlever_contributed(mech: Dict[str, Any]) -> bool:
    """Heuristic: did QLever contribute anything non-empty to mechanistic evidence (post-synthesis)?"""
    ez = mech.get("enzymes", {})
    nonempty_ez = any(
        (ez.get(s, {}).get(k) for s in ("a", "b") for k in ("substrate", "inhibitor", "inducer"))
    )
    return bool(
        nonempty_ez
        or mech.get("targets_a") or mech.get("targets_b")
        or mech.get("pathways_a") or mech.get("pathways_b")
        or mech.get("common_pathways")
    )


def _bool_qlever_contributed_raw(raw: Dict[str, Any]) -> bool:
    """Heuristic on the raw QLever block (pre-synthesis)."""
    ez = raw.get("enzymes", {})
    nonempty_ez = any((ez.get(s, {}).get(k) for s in ("a", "b") for k in ("substrate", "inhibitor", "inducer")))
    return bool(
        nonempty_ez
        or raw.get("targets_a") or raw.get("targets_b")
        or raw.get("pathways_a") or raw.get("pathways_b")
        or raw.get("common_pathways")
    )


# ----------------- public pipeline -----------------
def retrieve_and_normalize(
    drugA: str,
    drugB: str,
    *,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/openfda",
    topk_side_effects: int = 25,
    topk_faers: int = 10,
    topk_targets: int = 32,
    topk_pathways: int = 24,
) -> Dict[str, Any]:
    """Fetch raw evidence, normalize/synthesize PK/PD, and build the LLM context."""
    caveats: List[str] = []

    # 1) DuckDB init (idempotent)
    try:
        dq.init_duckdb_connection(parquet_dir)
    except Exception as e:
        caveats.append(f"DuckDB init failed: {e}")

    # NEW: instantiate DuckDBClient for method-based API
    db = dq.DuckDBClient(parquet_dir)

    a, b = drugA, drugB

    # 2) DuckDB: signals + DrugBank targets (PD fallback)
    prr_pair = None
    dili_a = dili_b = dict_a = dict_b = None
    diqt_a = diqt_b = None
    se_a_raw: List[str] = []
    se_b_raw: List[str] = []
    se_pair_raw: List[str] = []
    db_targets_a: List[str] = []
    db_targets_b: List[str] = []

    # Initialize semantic searcher (optional, graceful degradation if not available)
    semantic_searcher = get_semantic_searcher()
    use_semantic = semantic_searcher is not None
    
    # Try to build drug index if semantic search is available
    if use_semantic:
        try:
            # Get all unique drug names from database for indexing
            # This is a one-time operation, cached for subsequent queries
            all_drugs_query = """
                SELECT DISTINCT drug_a AS drug FROM twosides
                UNION
                SELECT DISTINCT drug_b AS drug FROM twosides
                UNION
                SELECT DISTINCT name_lower AS drug FROM drugbank
            """
            try:
                all_drugs = [row[0] for row in db._con.execute(all_drugs_query).fetchall() if row[0]]
                semantic_searcher.build_drug_index(all_drugs, force_rebuild=False)
            except Exception as e:
                # If indexing fails, continue without semantic search
                use_semantic = False
        except Exception:
            use_semantic = False
    
    # Helper function to find drugs with semantic search fallback
    def find_drug_with_semantic(query_drug: str) -> List[str]:
        """Find drug names using exact match first, then semantic search if needed."""
        candidates = [query_drug]
        
        # Try exact match first
        exact_match = db.get_side_effects(query_drug, top_k=1)
        if exact_match:
            return [query_drug]
        
        # If no exact match and semantic search available, try semantic
        if use_semantic and semantic_searcher:
            similar = semantic_searcher.search_similar_drugs(query_drug, top_k=3, threshold=0.6)
            if similar:
                candidates.extend([drug for drug, score in similar])
        
        return candidates
    
    try:
        # TwoSides side effects (single-drug & pair)
        # Get initial results (may be empty if exact match fails)
        se_a_raw = db.get_side_effects(a, top_k=topk_side_effects * 2) or []  # Get more for ranking
        se_b_raw = db.get_side_effects(b, top_k=topk_side_effects * 2) or []
        se_pair_raw = db.get_side_effects(a, b, top_k=topk_side_effects * 2) or []
        
        # If no results and semantic search available, try semantic matching
        if use_semantic and semantic_searcher:
            if not se_a_raw:
                similar_a = semantic_searcher.search_similar_drugs(a, top_k=5, threshold=0.6)
                for similar_drug, sim_score in similar_a:
                    similar_se = db.get_side_effects(similar_drug, top_k=topk_side_effects)
                    if similar_se:
                        se_a_raw.extend(similar_se)
                        break  # Use first successful match
            
            if not se_b_raw:
                similar_b = semantic_searcher.search_similar_drugs(b, top_k=5, threshold=0.6)
                for similar_drug, sim_score in similar_b:
                    similar_se = db.get_side_effects(similar_drug, top_k=topk_side_effects)
                    if similar_se:
                        se_b_raw.extend(similar_se)
                        break
        
        # Get pair PRR using the dedicated method
        prr_pair = db.get_interaction_score(a, b)
        if prr_pair == 0.0:
            prr_pair = None
        
        # Get PRR data for side effects (for relevance scoring)
        prr_data_a = {}
        prr_data_b = {}
        try:
            # Get PRR for each side effect
            for se in se_a_raw:
                rows = db._con.execute(
                    "SELECT MAX(prr) FROM twosides WHERE (drug_a = ? OR drug_b = ?) AND side_effect = ?",
                    [a.lower(), a.lower(), se]
                ).fetchall()
                if rows and rows[0][0]:
                    prr_data_a[se] = float(rows[0][0])
            
            for se in se_b_raw:
                rows = db._con.execute(
                    "SELECT MAX(prr) FROM twosides WHERE (drug_a = ? OR drug_b = ?) AND side_effect = ?",
                    [b.lower(), b.lower(), se]
                ).fetchall()
                if rows and rows[0][0]:
                    prr_data_b[se] = float(rows[0][0])
        except Exception:
            pass  # PRR data is optional for scoring

        # Risk scores - using legacy methods that return numeric scores for compatibility
        dili_a = db.get_dilirank_score(a)  # float or None
        dili_b = db.get_dilirank_score(b)
        dict_a = db.get_dictrank_score(a)  # float or None
        dict_b = db.get_dictrank_score(b)
        diqt_a = db.get_diqt_score(a)
        diqt_b = db.get_diqt_score(b)

        # DrugBank targets (PD fallback)
        db_targets_a = db.get_drug_targets(a) or []
        db_targets_b = db.get_drug_targets(b) or []

    except Exception as e:
        caveats.append(f"DuckDB retrieval failed: {e}")

    # 3) QLever mechanistic (enriched/basic/stub)
    qlev = _get_qlever_mechanistic_or_stub(a, b)

    # --- detect QLever raw contribution BEFORE synthesis
    ql_raw_contrib = _bool_qlever_contributed_raw(qlev)

    # 4) Merge QLever + DuckDB targets into a single mechanistic block (normalized)
    mech = synthesize_mechanistic(
        qlever_mech=qlev,
        fallback_targets_a=db_targets_a,
        fallback_targets_b=db_targets_b,
    )

    # 4b) Score and rank mechanistic evidence by relevance
    query_context = {
        "drug_a": a,
        "drug_b": b,
        "prr_pair": prr_pair,
        "dili_a": dili_a,
        "dili_b": dili_b,
        "dict_a": dict_a,
        "dict_b": dict_b,
    }
    
    # Score and rank targets
    targets_a = mech.get("targets_a", [])
    targets_b = mech.get("targets_b", [])
    overlap_targets = mech.get("common_targets", []) or []
    
    if targets_a:
        scored_targets_a = score_and_rank_targets(targets_a, query_context, overlap_targets)
        mech["targets_a"] = [t for t, _ in scored_targets_a[:topk_targets]]
    
    if targets_b:
        scored_targets_b = score_and_rank_targets(targets_b, query_context, overlap_targets)
        mech["targets_b"] = [t for t, _ in scored_targets_b[:topk_targets]]
    
    # Score and rank pathways
    pathways_a = mech.get("pathways_a", [])
    pathways_b = mech.get("pathways_b", [])
    common_pathways = mech.get("common_pathways", []) or []
    
    if pathways_a:
        scored_pathways_a = score_and_rank_pathways(pathways_a, query_context, common_pathways)
        mech["pathways_a"] = [p for p, _ in scored_pathways_a[:topk_pathways]]
    
    if pathways_b:
        scored_pathways_b = score_and_rank_pathways(pathways_b, query_context, common_pathways)
        mech["pathways_b"] = [p for p, _ in scored_pathways_b[:topk_pathways]]
    
    if common_pathways:
        scored_common = score_and_rank_pathways(common_pathways, query_context, common_pathways)
        mech["common_pathways"] = [p for p, _ in scored_common[:topk_pathways]]

    # 5) FAERS via OpenFDA (cached)
    faers_a: List[tuple] = []
    faers_b: List[tuple] = []
    faers_combo: List[tuple] = []
    try:
        ofda = OpenFDAClient(cache_dir=openfda_cache)
        faers_a = ofda.get_top_reactions(a, top_k=topk_faers)      # [(term, count)]
        faers_b = ofda.get_top_reactions(b, top_k=topk_faers)
        faers_combo = ofda.get_combination_reactions(a, b, top_k=topk_faers)
    except Exception as e:
        caveats.append(f"OpenFDA retrieval failed: {e}")

    # Enforce FAERS top-K even if client returns more
    faers_a = (faers_a or [])[:topk_faers]
    faers_b = (faers_b or [])[:topk_faers]
    faers_combo = (faers_combo or [])[:topk_faers]

    # Normalize FAERS to JSON-stable lists-of-lists so cache equals fresh
    faers_a = _to_pairs_list(faers_a)
    faers_b = _to_pairs_list(faers_b)
    faers_combo = _to_pairs_list(faers_combo)

    # 6) Build normalized context (matches llm_interface expectations)
    ql_contrib = _bool_qlever_contributed(mech)

    # Build caveats with explicit fallback note if QLever RAW contributed nothing
    caveats_agg = list(dict.fromkeys((qlev.get("caveats") or []) + caveats))
    if not ql_raw_contrib:
        caveats_agg.append("QLever mechanistic unavailable; using DuckDB DrugBank targets as PD fallback.")

    # Score and rank side effects by relevance
    query_context_se = {
        "drug_a": a,
        "drug_b": b,
        "prr_pair": prr_pair,
        "dili_a": dili_a,
        "dili_b": dili_b,
        "dict_a": dict_a,
        "dict_b": dict_b,
    }
    
    # Get semantic similarity scores if available
    semantic_scores_a = {}
    semantic_scores_b = {}
    if use_semantic and semantic_searcher:
        # Build side effect index if not already built
        all_side_effects = list(set(se_a_raw + se_b_raw + se_pair_raw))
        if all_side_effects:
            semantic_searcher.build_side_effect_index(all_side_effects, force_rebuild=False)
            
            # Get semantic scores for side effects
            for se in se_a_raw:
                similar = semantic_searcher.search_similar_side_effects(se, top_k=1, threshold=0.7)
                if similar and similar[0][0] == se:
                    semantic_scores_a[se] = similar[0][1]
            
            for se in se_b_raw:
                similar = semantic_searcher.search_similar_side_effects(se, top_k=1, threshold=0.7)
                if similar and similar[0][0] == se:
                    semantic_scores_b[se] = similar[0][1]
    
    # Score and rank side effects
    scored_se_a = score_and_rank_side_effects(se_a_raw, query_context_se, prr_data_a, semantic_scores_a)
    scored_se_b = score_and_rank_side_effects(se_b_raw, query_context_se, prr_data_b, semantic_scores_b)
    
    # Apply top-K after ranking
    side_effects_a = [se for se, _ in scored_se_a[:topk_side_effects]]
    side_effects_b = [se for se, _ in scored_se_b[:topk_side_effects]]
    
    # For pair-specific side effects, also score and rank
    if se_pair_raw:
        # Get PRR for pair side effects
        prr_data_pair = {}
        try:
            for se in se_pair_raw:
                rows = db._con.execute(
                    "SELECT MAX(prr) FROM twosides WHERE ((drug_a = ? AND drug_b = ?) OR (drug_a = ? AND drug_b = ?)) AND side_effect = ?",
                    [a.lower(), b.lower(), b.lower(), a.lower(), se]
                ).fetchall()
                if rows and rows[0][0]:
                    prr_data_pair[se] = float(rows[0][0])
        except Exception:
            pass
        
        scored_se_pair = score_and_rank_side_effects(se_pair_raw, query_context_se, prr_data_pair)
        se_pair_raw_ranked = [se for se, _ in scored_se_pair[:topk_side_effects]]
    else:
        se_pair_raw_ranked = []

    # Compute PK/PD summary first to check for canonical interactions
    pkpd = _jsonify_sets(summarize_pkpd_risk(a, b, mech))
    has_canonical = bool(pkpd.get("pk_detail", {}).get("canonical_interaction"))

    context: Dict[str, Any] = {
        "drugs": {
            "a": {"name": a, "synonyms": qlev.get("synonyms_a", []), "ids": qlev.get("ids_a", {})},
            "b": {"name": b, "synonyms": qlev.get("synonyms_b", []), "ids": qlev.get("ids_b", {})},
        },
        "signals": {
            "tabular": {
                "prr": prr_pair,                 # pair PRR proxy (max PRR among pair side-effects), may be None
                "side_effects_a": side_effects_a,
                "side_effects_b": side_effects_b,
                "dili_a": dili_a if dili_a is not None else "unknown",
                "dili_b": dili_b if dili_b is not None else "unknown",
                "dict_a": dict_a if dict_a is not None else "unknown",
                "dict_b": dict_b if dict_b is not None else "unknown",
                "diqt_a": diqt_a,
                "diqt_b": diqt_b,
            },
            "mechanistic": mech,
            "faers": {
                "top_reactions_a": faers_a,
                "top_reactions_b": faers_b,
                "combo_reactions": faers_combo,
            },
        },
        "caveats": caveats_agg,
        "pkpd": pkpd,
        "sources": {
            "duckdb": ["TwoSides", "DILIrank", "DICTRank", "DIQT", "DrugBank"],
            "qlever": ["PubChem RDF via QLever"] if ql_raw_contrib else ["PubChem RDF via QLever (limited)"],
            "openfda": ["FAERS via OpenFDA (cached)"],
            "apis": ["PubChem REST API", "UniProt REST API", "KEGG REST API", "Reactome REST API"],
            "canonical": ["Canonical PK/PD Dictionary"] if has_canonical else [],
            "semantic": ["Semantic Search (Embeddings)"] if use_semantic else [],
        },
        "meta": {
            "drug_pair": f"{drugA}|{drugB}",
            "pair_key": _pair_key(drugA, drugB),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "version": VERSION,
            "qlever_contributed_raw": ql_raw_contrib,
            "qlever_contributed": ql_contrib,
        },
    }

    return context


def get_context_cached(
    drugA: str,
    drugB: str,
    *,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/openfda",
    force_refresh: bool = False,
    topk_side_effects: int = 25,
    topk_faers: int = 10,
    topk_targets: int = 32,
    topk_pathways: int = 24,
) -> Tuple[Dict[str, Any], str]:
    """
    Cache the normalized context (structured JSON) because retrieval is slow.
    Cache key is based on an unordered drug pair and VERSION, so A+B == B+A.
    """
    key = _sha({"pair": _pair_key(drugA, drugB), "v": VERSION})
    path = _ctx_path(key)
    if (not force_refresh) and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), key

    ctx = retrieve_and_normalize(
        drugA,
        drugB,
        parquet_dir=parquet_dir,
        openfda_cache=openfda_cache,
        topk_side_effects=topk_side_effects,
        topk_faers=topk_faers,
        topk_targets=topk_targets,
        topk_pathways=topk_pathways,
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)
    return ctx, key


def get_response_cached(
    context: Dict[str, Any],
    *,
    mode: str,
    seed: Optional[int],
    temperature: float,
    model_name: str = LLM_MODEL_NAME,
) -> Dict[str, Any]:
    """
    Cache LLM responses keyed by (context, mode, seed, temperature, model_name).
    Useful for demos & repeated queries.
    """
    key = _sha({
        "ctx": context,
        "mode": mode,
        "seed": seed,
        "temp": round(float(temperature), 3),
        "model": model_name,
        "v": VERSION,
    })
    path = _resp_path(key)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    out = generate_response(context, mode, seed=seed, temperature=temperature)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def run_rag(
    drugA: str,
    drugB: str,
    *,
    mode: str = "Patient",
    seed: Optional[int] = 42,
    temperature: float = 0.2,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/openfda",
    history: Optional[List[Dict[str, str]]] = None,
    use_cache_context: bool = True,
    use_cache_response: bool = False,
    topk_side_effects: int = 25,
    topk_faers: int = 10,
    topk_targets: int = 32,
    topk_pathways: int = 24,
    model_name: Optional[str] = None,  # Override default model
) -> Dict[str, Any]:
    """High-level entry point the UI can call."""
    if use_cache_context:
        context, _ = get_context_cached(
            drugA,
            drugB,
            parquet_dir=parquet_dir,
            openfda_cache=openfda_cache,
            topk_side_effects=topk_side_effects,
            topk_faers=topk_faers,
            topk_targets=topk_targets,
            topk_pathways=topk_pathways,
        )
    else:
        context = retrieve_and_normalize(
            drugA,
            drugB,
            parquet_dir=parquet_dir,
            openfda_cache=openfda_cache,
            topk_side_effects=topk_side_effects,
            topk_faers=topk_faers,
            topk_targets=topk_targets,
            topk_pathways=topk_pathways,
        )

    # Use provided model_name or fall back to default
    selected_model = model_name or LLM_MODEL_NAME

    if use_cache_response:
        answer = get_response_cached(
            context,
            mode=mode,
            seed=seed,
            temperature=temperature,
            model_name=selected_model,
        )
    else:
        answer = generate_response(
            context,
            mode,
            seed=seed,
            temperature=temperature,
            history=history or [],
            model_name=selected_model,
        )

    return {"context": context, "answer": answer}


# ----------------- small cache helpers (optional) -----------------
def clear_context_cache(drugA: str, drugB: str) -> bool:
    """Delete the cached context for a pair (unordered). Returns True if removed."""
    key = _sha({"pair": _pair_key(drugA, drugB), "v": VERSION})
    path = _ctx_path(key)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def load_pkpd_from_cache(drugA: str, drugB: str) -> Optional[Dict[str, Any]]:
    """Convenience accessor to load only the PK/PD block from cache, if present."""
    key = _sha({"pair": _pair_key(drugA, drugB), "v": VERSION})
    path = _ctx_path(key)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        ctx = json.load(f)
    return ctx.get("pkpd")
