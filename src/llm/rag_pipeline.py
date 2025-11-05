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

# PK/PD synthesis utilities
from src.utils.pkpd_utils import (
    synthesize_mechanistic,
    summarize_pkpd_risk,
)

# LLM interface (import model name so response cache keys are stable)
from src.llm.llm_interface import generate_response, MODEL_NAME as LLM_MODEL_NAME

# ----------------- versioning & caching -----------------
VERSION = 3  # bump when schema/logic changes to invalidate old cached contexts

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
        # fall through to basic
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

    a, b = drugA, drugB

    # 2) DuckDB: signals + DrugBank targets (PD fallback)
    prr = dili_a = dili_b = dict_a = dict_b = None
    diqt_a = diqt_b = None
    se_a: List[str] = []
    se_b: List[str] = []
    db_targets_a: List[str] = []
    db_targets_b: List[str] = []

    try:
        prr          = dq.get_interaction_score(a, b)
        dili_a       = dq.get_dili_risk(a) or "unknown"
        dili_b       = dq.get_dili_risk(b) or "unknown"
        dict_a       = dq.get_dict_rank(a) or "unknown"
        dict_b       = dq.get_dict_rank(b) or "unknown"
        diqt_a       = dq.get_diqt_score(a)
        diqt_b       = dq.get_diqt_score(b)
        se_a         = dq.get_side_effects(a)[:topk_side_effects]
        se_b         = dq.get_side_effects(b)[:topk_side_effects]
        db_targets_a = dq.get_drug_targets(a)  # list[str]
        db_targets_b = dq.get_drug_targets(b)  # list[str]
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

    # 4b) Trim mechanistic lists to keep context lean for the LLM
    mech["targets_a"] = mech.get("targets_a", [])[:topk_targets]
    mech["targets_b"] = mech.get("targets_b", [])[:topk_targets]
    mech["pathways_a"] = mech.get("pathways_a", [])[:topk_pathways]
    mech["pathways_b"] = mech.get("pathways_b", [])[:topk_pathways]
    mech["common_pathways"] = mech.get("common_pathways", [])[:topk_pathways]

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
    # (post-synthesis contribution, kept for meta/debug)
    ql_contrib = _bool_qlever_contributed(mech)

    # Build caveats with explicit fallback note if QLever RAW contributed nothing
    caveats_agg = list(dict.fromkeys((qlev.get("caveats") or []) + caveats))
    if not ql_raw_contrib:
        caveats_agg.append("QLever mechanistic unavailable; using DuckDB DrugBank targets as PD fallback.")

    context: Dict[str, Any] = {
        "drugs": {
            "a": {"name": a, "synonyms": qlev.get("synonyms_a", []), "ids": qlev.get("ids_a", {})},
            "b": {"name": b, "synonyms": qlev.get("synonyms_b", []), "ids": qlev.get("ids_b", {})},
        },
        "signals": {
            "tabular": {
                "prr": prr,
                "side_effects_a": se_a,
                "side_effects_b": se_b,
                "dili_a": dili_a or "unknown",
                "dili_b": dili_b or "unknown",
                "dict_a": dict_a or "unknown",
                "dict_b": dict_b or "unknown",
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
        "sources": {
            "duckdb": ["TwoSides", "DILIrank", "DICTRank", "DIQT", "DrugBank"],
            # reflect QLever RAW contribution status
            "qlever": ["PubChem RDF via QLever"] if ql_raw_contrib else ["PubChem RDF via QLever (limited)"],
            "openfda": ["FAERS via OpenFDA (cached)"],
        },
        "caveats": caveats_agg,
        "pkpd": _jsonify_sets(summarize_pkpd_risk(a, b, mech)),  # <-- JSON-safe PK/PD
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

    if use_cache_response:
        answer = get_response_cached(
            context,
            mode=mode,
            seed=seed,
            temperature=temperature,
            model_name=LLM_MODEL_NAME,
        )
    else:
        answer = generate_response(
            context,
            mode,
            seed=seed,
            temperature=temperature,
            history=history or [],
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
