# src/llm/rag_pipeline.py
from __future__ import annotations

import os
import json
import hashlib
from typing import Any, Dict, Tuple, Optional, List

# Retrieval modules
from src.retrieval import duckdb_query as dq
from src.retrieval.openfda_api import OpenFDAClient
from src.retrieval import qlever_query as ql  # now defines get_mechanistic()

# PK/PD synthesis utilities
from src.utils.pkpd_utils import (
    synthesize_mechanistic,
    summarize_pkpd_risk,
)

# LLM interface (import model name so response cache keys are stable)
from src.llm.llm_interface import generate_response, MODEL_NAME as LLM_MODEL_NAME

# ----------------- simple JSON caching -----------------
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


# ----------------- retrieval helpers -----------------
def _get_qlever_mechanistic_or_stub(drugA: str, drugB: str) -> Dict[str, Any]:
    """
    Try QLever.get_mechanistic(); on error, return a minimal stub.
    The stub leaves enzymes/targets/pathways empty; PD targets will be filled from
    DuckDB DrugBank via synthesize_mechanistic().
    """
    try:
        return ql.get_mechanistic(drugA, drugB)
    except Exception:
        return {
            "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                        "b": {"substrate": [], "inhibitor": [], "inducer": []}},
            "targets_a": [], "targets_b": [],
            "pathways_a": [], "pathways_b": [],
            "common_pathways": [],
            "ids_a": {}, "ids_b": {},
            "synonyms_a": [], "synonyms_b": [],
            "caveats": ["QLever mechanistic data unavailable; using DuckDB DrugBank targets as PD fallback."],
        }


# ----------------- public pipeline -----------------
def retrieve_and_normalize(
    drugA: str,
    drugB: str,
    *,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/openfda",
) -> Dict[str, Any]:
    """Fetch raw evidence, normalize/synthesize PK/PD, and build the LLM context."""
    # 1) DuckDB init (idempotent)
    dq.init_duckdb_connection(parquet_dir)

    a, b = drugA, drugB

    # 2) DuckDB: signals + DrugBank targets (PD fallback)
    prr          = dq.get_interaction_score(a, b)
    dili_a       = dq.get_dili_risk(a) or "unknown"
    dili_b       = dq.get_dili_risk(b) or "unknown"
    dict_a       = dq.get_dict_rank(a) or "unknown"
    dict_b       = dq.get_dict_rank(b) or "unknown"
    diqt_a       = dq.get_diqt_score(a)
    diqt_b       = dq.get_diqt_score(b)
    se_a         = dq.get_side_effects(a)[:25]
    se_b         = dq.get_side_effects(b)[:25]
    db_targets_a = dq.get_drug_targets(a)  # list[str]
    db_targets_b = dq.get_drug_targets(b)  # list[str]

    # 3) QLever mechanistic (or stub)
    qlev = _get_qlever_mechanistic_or_stub(a, b)

    # 4) Merge QLever + DuckDB targets into a single mechanistic block
    mech = synthesize_mechanistic(
        qlever_mech=qlev,
        fallback_targets_a=db_targets_a,
        fallback_targets_b=db_targets_b,
    )

    # 5) FAERS via OpenFDA (cached)
    ofda = OpenFDAClient(cache_dir=openfda_cache)
    faers_a = ofda.get_top_reactions(a, top_k=10)      # [(term, count)]
    faers_b = ofda.get_top_reactions(b, top_k=10)
    faers_combo = ofda.get_combination_reactions(a, b, top_k=10)

    # 6) Build normalized context (matches llm_interface expectations)
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
                "dili_a": dili_a,
                "dili_b": dili_b,
                "dict_a": dict_a,
                "dict_b": dict_b,
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
            "qlever": (["PubChem RDF subset"]),  # present; stub or real behind get_mechanistic
            "openfda": ["FAERS via OpenFDA (cached)"],
        },
        "caveats": list(
            dict.fromkeys(  # de-dup while preserving order
                (qlev.get("caveats") or [])
            )
        ),
    }

    # 7) Optional: tuck synthesized PK/PD summaries (can help the model)
    context["pkpd"] = summarize_pkpd_risk(a, b, mech)

    return context


def get_context_cached(
    drugA: str,
    drugB: str,
    *,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/openfda",
    force_refresh: bool = False,
) -> Tuple[Dict[str, Any], str]:
    """Cache the normalized context because retrieval can be the slowest step."""
    key = _sha({"a": drugA.lower(), "b": drugB.lower(), "v": 1})
    path = _ctx_path(key)
    if (not force_refresh) and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), key
    ctx = retrieve_and_normalize(drugA, drugB, parquet_dir=parquet_dir, openfda_cache=openfda_cache)
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
        "v": 1,
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
) -> Dict[str, Any]:
    """High-level entry point the UI can call."""
    if use_cache_context:
        context, _ = get_context_cached(
            drugA, drugB, parquet_dir=parquet_dir, openfda_cache=openfda_cache
        )
    else:
        context = retrieve_and_normalize(
            drugA, drugB, parquet_dir=parquet_dir, openfda_cache=openfda_cache
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
