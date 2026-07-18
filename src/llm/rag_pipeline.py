# src/llm/rag_pipeline.py
from __future__ import annotations

import os
import json
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from dataclasses import asdict
from typing import Any, Callable, Dict, Tuple, Optional, List
from datetime import datetime, timezone
from collections.abc import Mapping  # <-- for _jsonify_sets

LOG = logging.getLogger(__name__)

from src.config.data_policy import get_source_status
from src.config.settings import get_settings

# Retrieval modules
from src.retrieval import duckdb_query as dq
from src.retrieval.openfda_api import OpenFDAClient
from src.retrieval import qlever_query as ql  # may expose get_mechanistic_enriched / get_mechanistic
from src.retrieval.semantic_search import get_semantic_searcher, SemanticSearcher

# PK/PD synthesis utilities
from src.utils.pkpd_utils import (
    canonicalize_enzyme,
    synthesize_mechanistic,
    summarize_pkpd_risk,
)
from src.utils.relevance_scoring import (
    score_and_rank_side_effects,
    score_and_rank_pathways,
    score_and_rank_targets,
    merge_and_rerank_evidence,
)
from src.utils.query_expansion import (
    expand_drug_query,
    expand_drug_pair_queries,
    get_best_match_from_expanded,
    merge_expanded_results,
    create_expanded_query_context,
)
from src.retrieval.hybrid_search import hybrid_search_drugs, adaptive_hybrid_search
from src.utils.adaptive_retrieval import adaptive_retrieve, calculate_result_quality
from src.utils.reranking import get_reranker
from src.utils.feedback_loops import get_feedback_tracker
from src.utils.context_filtering import filter_context_by_relevance, filter_context_sections

# LLM interface
from src.llm.llm_interface import generate_response, MODEL_NAME as LLM_MODEL_NAME

# ----------------- versioning & caching -----------------
# Version 12: Adds NCI-ALMANAC parquet evidence under signals.tabular.nci_almanac.
# Version 11: Adds API-first research enrichment under signals.research_enrichment
#             for FDA PGx, Europe PMC, Open Targets, STRING, and optional BioGRID.
# Version 10: Adds openFDA label, DailyMed, RxNorm/RxClass, and FDA CYP/transporter
#             reference evidence under signals.clinical_reference.
VERSION = 12  # bump when schema/logic changes to invalidate old cached contexts
# Version 9: OpenFDA cache moved under data/cache; public REST enrichment schema
#            now includes required PubChem IDs and source-specific enrichment slots.
# Version 8: FORCED ENABLEMENT of all RAG features - semantic search, reranking, query expansion,
#            hybrid search, and adaptive retrieval are now REQUIRED (not optional)
# Version 7: Added hybrid search, adaptive retrieval, reranking, feedback loops, context filtering
# Version 6: Added query expansion with synonyms, variations, and semantic similarity
# Version 5: Added semantic search with embeddings and relevance scoring/ranking
# Version 4: Added KEGG/Reactome/UniProt API integrations, enhanced PK/PD summarization,
#            PubChem PK data, target enrichment, pathway enhancements

CACHE_DIR = os.path.join("data", "cache")
CTX_DIR   = os.path.join(CACHE_DIR, "contexts")
# Legacy path retained for external callers that may still monkeypatch/import it.
# New LLM response text is not written to disk.
RESP_DIR  = os.path.join(CACHE_DIR, "responses")
os.makedirs(CTX_DIR, exist_ok=True)


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


_CACHE_SAFE = re.compile(r"[^a-z0-9]+")


def _cache_slug(value: str) -> str:
    slug = _CACHE_SAFE.sub("_", (value or "").strip().lower()).strip("_")
    return slug[:80] or "unknown"


def _context_cache_key(drugA: str, drugB: str) -> str:
    """Readable cache filename stem preserving the first entered pair order."""
    return f"{_cache_slug(drugA)}_{_cache_slug(drugB)}"


def _legacy_context_cache_key(drugA: str, drugB: str) -> str:
    """Previous opaque context cache key retained for one-way migration."""
    return _sha({"pair": _pair_key(drugA, drugB), "v": VERSION})


def _context_cache_candidates(drugA: str, drugB: str) -> List[str]:
    preferred = _context_cache_key(drugA, drugB)
    reverse = _context_cache_key(drugB, drugA)
    legacy = _legacy_context_cache_key(drugA, drugB)
    out: List[str] = []
    for key in (preferred, reverse, legacy):
        if key not in out:
            out.append(key)
    return out


def _context_cache_version_ok(ctx: Dict[str, Any]) -> bool:
    return (ctx.get("meta") or {}).get("version") == VERSION


def _find_context_cache_by_pair(drugA: str, drugB: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    pair_key = _pair_key(drugA, drugB)
    if not os.path.isdir(CTX_DIR):
        return None, None
    for name in os.listdir(CTX_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(CTX_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                ctx = json.load(f)
        except Exception:
            continue
        meta = ctx.get("meta") or {}
        if meta.get("pair_key") == pair_key and _context_cache_version_ok(ctx):
            return ctx, os.path.splitext(name)[0]
    return None, None


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
def _qlever_disabled_stub(reason: str = "QLever RDF disabled for NVIDIA demo runtime.") -> Dict[str, Any]:
    return {
        "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                    "b": {"substrate": [], "inhibitor": [], "inducer": []}},
        "targets_a": [], "targets_b": [],
        "pathways_a": [], "pathways_b": [],
        "common_pathways": [],
        "ids_a": {}, "ids_b": {},
        "synonyms_a": [], "synonyms_b": [],
        "caveats": [reason],
    }


def _get_qlever_mechanistic_or_stub(drugA: str, drugB: str) -> Dict[str, Any]:
    """
    Prefer ql.get_mechanistic_enriched(), fall back to ql.get_mechanistic().
    On error, return a minimal stub. synthesize_mechanistic() will add PD fallbacks.
    """
    settings = get_settings()
    if not settings.enable_qlever:
        return _qlever_disabled_stub()

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


def _iter_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, Mapping):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _text_value(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in (
            "label",
            "name",
            "displayName",
            "protein_name",
            "uniprot_id",
            "primaryAccession",
            "accession",
            "pathway_name",
            "pathway_id",
            "enzyme_name",
            "enzyme_id",
            "id",
            "uri",
        ):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value).strip()


def _append_unique(existing: Any, additions: Any, *, limit: Optional[int] = None) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in _iter_list(existing) + _iter_list(additions):
        text = _text_value(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if limit is not None and len(out) >= limit:
            break
    return out


def _empty_chembl_side() -> Dict[str, Any]:
    return {
        "enzymes": {},
        "enzyme_strength": {"strong": [], "moderate": [], "weak": []},
        "chembl_validation": {"found": False, "matches": [], "mismatches": []},
    }


def _normalize_chembl_side(value: Any) -> Dict[str, Any]:
    out = _empty_chembl_side()
    if not isinstance(value, Mapping):
        return out

    out.update(dict(value))
    strengths = value.get("enzyme_strength") if isinstance(value.get("enzyme_strength"), Mapping) else {}
    out["enzyme_strength"] = {
        "strong": _append_unique([], strengths.get("strong")),
        "moderate": _append_unique([], strengths.get("moderate")),
        "weak": _append_unique([], strengths.get("weak")),
    }
    validation = value.get("chembl_validation") if isinstance(value.get("chembl_validation"), Mapping) else {}
    out["chembl_validation"] = {
        **dict(validation),
        "found": bool(validation.get("found")),
        "matches": _append_unique([], validation.get("matches")),
        "mismatches": _append_unique([], validation.get("mismatches")),
    }
    if not isinstance(out.get("enzymes"), Mapping):
        out["enzymes"] = {}
    return out


def _normalize_chembl_enrichment(value: Any) -> Dict[str, Any]:
    enrichment = value if isinstance(value, Mapping) else {}
    return {
        "a": _normalize_chembl_side(enrichment.get("a")),
        "b": _normalize_chembl_side(enrichment.get("b")),
    }


def _chembl_side_has_content(value: Any) -> bool:
    side = _normalize_chembl_side(value)
    validation = side.get("chembl_validation") or {}
    strengths = side.get("enzyme_strength") or {}
    return bool(
        validation.get("found")
        or validation.get("matches")
        or validation.get("mismatches")
        or any(strengths.get(key) for key in ("strong", "moderate", "weak"))
    )


def _ensure_enrichment_schema(mech: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(mech.get("enzymes"), Mapping):
        mech["enzymes"] = {}
    enzymes = mech["enzymes"]
    for side in ("a", "b"):
        if not isinstance(enzymes.get(side), Mapping):
            enzymes[side] = {}
        side_roles = enzymes[side]
        for role in ("substrate", "inhibitor", "inducer"):
            side_roles[role] = _append_unique([], side_roles.get(role))

    for key in ("ids_a", "ids_b", "pk_data_a", "pk_data_b"):
        if not isinstance(mech.get(key), Mapping):
            mech[key] = {}

    for key in (
        "synonyms_a",
        "synonyms_b",
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
    ):
        if not isinstance(mech.get(key), list):
            mech[key] = _iter_list(mech.get(key))

    mech["chembl_enrichment"] = _normalize_chembl_enrichment(mech.get("chembl_enrichment"))
    return mech


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _run_parallel_source_tasks(
    tasks: Dict[str, Callable[[], Any]],
    *,
    timeout_s: float,
    caveats: List[str],
    label: str,
    max_workers: int = 6,
) -> Dict[str, Any]:
    """Run independent source lookups with a bounded wait and return partial results."""
    if not tasks:
        return {}

    results: Dict[str, Any] = {}
    worker_count = max(1, min(max_workers, len(tasks)))
    executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="infermed-source")
    futures = {executor.submit(fn): name for name, fn in tasks.items()}

    try:
        for future in as_completed(futures, timeout=max(0.1, timeout_s)):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                caveats.append(f"{name} enrichment failed: {exc}")
    except FuturesTimeout:
        pending = [name for future, name in futures.items() if not future.done()]
        if pending:
            caveats.append(
                f"{label} timed out after {timeout_s:.1f}s; using partial results "
                f"without {', '.join(pending)}."
            )
        for future in futures:
            if not future.done():
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return results


def _pathway_names(rows: Any) -> List[str]:
    out: List[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            name = row.get("pathway_name") or row.get("name") or row.get("displayName")
            pathway_id = row.get("pathway_id") or row.get("id") or row.get("stId") or row.get("dbId")
            if name:
                out.append(str(name))
            elif pathway_id:
                out.append(str(pathway_id))
        else:
            text = str(row).strip()
            if text:
                out.append(text)
    return out


def _enzyme_tokens_from_kegg(rows: Any) -> List[str]:
    tokens: List[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("enzyme_name") or "").strip()
        if not text:
            continue
        match = re.search(r"(?i)(?:cyp|cytochrome\s*p450)\s*(\d+[a-z]?\d*)", text)
        if match:
            tokens.append(f"cyp{match.group(1).lower()}")
        elif text:
            tokens.append(canonicalize_enzyme(text))
    return _append_unique([], tokens)


def _merge_chembl_inhibitors(mech: Dict[str, Any], enrichment: Dict[str, Any]) -> None:
    enzymes = mech.setdefault("enzymes", {"a": {}, "b": {}})
    for side in ("a", "b"):
        side_data = enrichment.get(side)
        if not isinstance(side_data, dict):
            continue
        strengths = side_data.get("enzyme_strength") or {}
        inhibitors = []
        for strength in ("strong", "moderate", "weak"):
            inhibitors.extend(strengths.get(strength) or [])
        if inhibitors:
            side_roles = enzymes.setdefault(side, {})
            side_roles["inhibitor"] = _append_unique(side_roles.get("inhibitor"), inhibitors)


_UNIPROT_ACCESSION = re.compile(r"\b[OPQ][0-9][A-Z0-9]{3}[0-9]\b|\b[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9]\b", re.I)


def _record_key(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in (
            "uniprot_id",
            "primaryAccession",
            "accession",
            "pathway_id",
            "stId",
            "dbId",
            "enzyme_id",
            "enzyme_name",
            "original_id",
            "name",
            "displayName",
        ):
            text = str(value.get(key) or "").strip()
            if text:
                return text.lower()
    return _text_value(value).lower()


def _merge_records(existing: Any, additions: Any, *, limit: Optional[int] = None) -> List[Any]:
    out: List[Any] = []
    seen = set()
    for item in _iter_list(existing) + _iter_list(additions):
        key = _record_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if limit is not None and len(out) >= limit:
            break
    return out


def _extract_uniprot_accessions(values: Any) -> List[str]:
    accessions: List[str] = []
    for item in _iter_list(values):
        if isinstance(item, Mapping):
            for key in ("uniprot_id", "primaryAccession", "accession"):
                text = str(item.get(key) or "").strip()
                if text:
                    accessions.append(text)
        else:
            text = str(item or "")
            accessions.extend(match.group(0).upper() for match in _UNIPROT_ACCESSION.finditer(text))
    return _append_unique([], accessions)


def _uniprot_seed_terms(mech: Dict[str, Any], side: str) -> List[str]:
    seeds: List[Any] = []
    seeds.extend(_iter_list(mech.get(f"uniprot_ids_{side}")))
    seeds.extend(_iter_list(mech.get(f"uniprot_targets_{side}")))
    seeds.extend(_iter_list(mech.get(f"targets_{side}")))

    enzymes = (mech.get("enzymes") or {}).get(side) or {}
    if isinstance(enzymes, Mapping):
        for role in ("substrate", "inhibitor", "inducer"):
            seeds.extend(_iter_list(enzymes.get(role)))

    for row in _iter_list(mech.get(f"kegg_enzymes_{side}")):
        if isinstance(row, Mapping):
            seeds.append(row.get("enzyme_name") or row.get("enzyme_id"))
        else:
            seeds.append(row)

    chembl_side = (mech.get("chembl_enrichment") or {}).get(side) or {}
    if isinstance(chembl_side, Mapping):
        strengths = chembl_side.get("enzyme_strength") or {}
        if isinstance(strengths, Mapping):
            for strength in ("strong", "moderate", "weak"):
                seeds.extend(_iter_list(strengths.get(strength)))
        validation = chembl_side.get("chembl_validation") or {}
        if isinstance(validation, Mapping):
            seeds.extend(_iter_list(validation.get("matches")))
            seeds.extend(_iter_list(validation.get("mismatches")))

    return _append_unique([], seeds, limit=12)


def _enrich_mechanistic_with_public_rest(
    drugA: str,
    drugB: str,
    mech: Dict[str, Any],
    settings: Any,
    caveats: List[str],
) -> Dict[str, Any]:
    """
    Fill public-safe PK/PD gaps using source-specific REST clients.

    QLever remains the richest PubChem RDF path, but public-safe mode must still
    resolve identities and mechanism seeds without relying on SPARQL/RDF.
    """
    mech = dict(mech or {})
    _ensure_enrichment_schema(mech)
    rest_timeout_s = _env_float("INFERMED_PUBLIC_REST_TIMEOUT_S", 35.0)

    phase_tasks: Dict[str, Callable[[], Any]] = {}
    if settings.enable_pubchem_rest:
        def _pubchem_rest() -> Dict[str, Any]:
            from src.retrieval import pubchem_client as pc

            out: Dict[str, Any] = {}
            for side, drug in (("a", drugA), ("b", drugB)):
                ids_key = f"ids_{side}"
                pk_key = f"pk_data_{side}"
                ids = dict(mech.get(ids_key) or {})
                pk_data = dict(mech.get(pk_key) or {})
                cid = ids.get("pubchem_cid") or ids.get("pubchem") or ids.get("cid")
                if not cid:
                    cid = pc.get_compound_cid_by_name(drug)
                    if cid:
                        ids["pubchem_cid"] = str(cid)
                if not pk_data:
                    if cid:
                        pk_data = pc.get_compound_pk_data(str(cid)) or {}
                    else:
                        pk_data = pc.get_compound_pk_data_by_name(drug) or {}
                out[side] = {"ids": ids, "pk_data": pk_data}
            return out

        phase_tasks["PubChem REST API"] = _pubchem_rest

    if settings.enable_kegg:
        def _kegg_rest() -> Dict[str, Any]:
            from src.retrieval import kegg_client as kg

            return {
                "pathways_a": kg.get_drug_pathways(drugA),
                "pathways_b": kg.get_drug_pathways(drugB),
                "common_pathways": kg.get_common_pathways(drugA, drugB),
                "enzymes_a": kg.get_drug_enzymes(drugA),
                "enzymes_b": kg.get_drug_enzymes(drugB),
            }

        phase_tasks["KEGG REST API"] = _kegg_rest

    if settings.enable_chembl:
        def _chembl_rest() -> Dict[str, Any]:
            from src.retrieval import chembl_client as chembl

            enzymes = mech.get("enzymes") or {}
            enrichment = _normalize_chembl_enrichment(mech.get("chembl_enrichment"))
            if not _chembl_side_has_content(enrichment.get("a")):
                enrichment["a"] = _normalize_chembl_side(chembl.enrich_mechanistic_data(drugA, enzymes.get("a", {}) or {}))
            if not _chembl_side_has_content(enrichment.get("b")):
                enrichment["b"] = _normalize_chembl_side(chembl.enrich_mechanistic_data(drugB, enzymes.get("b", {}) or {}))
            return enrichment

        phase_tasks["ChEMBL REST API"] = _chembl_rest

    phase_results = _run_parallel_source_tasks(
        phase_tasks,
        timeout_s=rest_timeout_s,
        caveats=caveats,
        label="Public mechanistic source enrichment",
        max_workers=3,
    )

    pubchem = phase_results.get("PubChem REST API") or {}
    for side in ("a", "b"):
        side_data = pubchem.get(side) or {}
        ids = side_data.get("ids")
        pk_data = side_data.get("pk_data")
        if isinstance(ids, Mapping) and ids:
            mech[f"ids_{side}"] = dict(mech.get(f"ids_{side}") or {}, **dict(ids))
        if isinstance(pk_data, Mapping) and pk_data:
            mech[f"pk_data_{side}"] = dict(pk_data)

    kegg = phase_results.get("KEGG REST API") or {}
    kegg_pathways_a = kegg.get("pathways_a") or []
    kegg_pathways_b = kegg.get("pathways_b") or []
    kegg_common = kegg.get("common_pathways") or []
    kegg_enzymes_a = kegg.get("enzymes_a") or []
    kegg_enzymes_b = kegg.get("enzymes_b") or []
    if kegg_pathways_a:
        mech["kegg_pathways_a"] = kegg_pathways_a[:5]
        mech["pathways_a"] = _append_unique(mech.get("pathways_a"), _pathway_names(kegg_pathways_a), limit=24)
    if kegg_pathways_b:
        mech["kegg_pathways_b"] = kegg_pathways_b[:5]
        mech["pathways_b"] = _append_unique(mech.get("pathways_b"), _pathway_names(kegg_pathways_b), limit=24)
    if kegg_common:
        mech["kegg_common_pathways"] = kegg_common[:5]
        mech["common_pathways"] = _append_unique(mech.get("common_pathways"), _pathway_names(kegg_common), limit=24)
    if kegg_enzymes_a:
        mech["kegg_enzymes_a"] = kegg_enzymes_a[:8]
        side_roles = mech["enzymes"].setdefault("a", {})
        side_roles["substrate"] = _append_unique(side_roles.get("substrate"), _enzyme_tokens_from_kegg(kegg_enzymes_a))
    if kegg_enzymes_b:
        mech["kegg_enzymes_b"] = kegg_enzymes_b[:8]
        side_roles = mech["enzymes"].setdefault("b", {})
        side_roles["substrate"] = _append_unique(side_roles.get("substrate"), _enzyme_tokens_from_kegg(kegg_enzymes_b))

    chembl_enrichment = phase_results.get("ChEMBL REST API")
    if chembl_enrichment:
        enrichment = _normalize_chembl_enrichment(chembl_enrichment)
        mech["chembl_enrichment"] = enrichment
        _merge_chembl_inhibitors(mech, enrichment)

    if settings.enable_uniprot:
        def _uniprot_rest() -> Dict[str, Any]:
            from src.retrieval import uniprot_client as uc

            out: Dict[str, Any] = {}
            for side in ("a", "b"):
                seeds = _uniprot_seed_terms(mech, side)
                if not seeds:
                    continue
                enriched = uc.enrich_protein_list(seeds[:8])
                if enriched:
                    out[side] = {
                        "targets": enriched,
                        "ids": _extract_uniprot_accessions(enriched),
                    }
            return out

        uniprot = _run_parallel_source_tasks(
            {"UniProt REST API": _uniprot_rest},
            timeout_s=rest_timeout_s,
            caveats=caveats,
            label="UniProt target enrichment",
            max_workers=1,
        ).get("UniProt REST API") or {}
        for side in ("a", "b"):
            side_data = uniprot.get(side) or {}
            if side_data.get("targets"):
                mech[f"uniprot_targets_{side}"] = _merge_records(
                    mech.get(f"uniprot_targets_{side}"),
                    side_data.get("targets"),
                    limit=12,
                )
            if side_data.get("ids"):
                mech[f"uniprot_ids_{side}"] = _append_unique(
                    mech.get(f"uniprot_ids_{side}"),
                    side_data.get("ids"),
                    limit=12,
                )

    if settings.enable_reactome:
        def _reactome_rest() -> Dict[str, Any]:
            from src.retrieval import reactome_client as rc

            out: Dict[str, Any] = {}
            for side, drug in (("a", drugA), ("b", drugB)):
                uniprot_ids = _append_unique([], mech.get(f"uniprot_ids_{side}"), limit=12)
                if not uniprot_ids:
                    continue
                pathways = rc.get_drug_target_pathways(drug, uniprot_ids[:8])
                if pathways:
                    out[side] = pathways
            return out

        reactome = _run_parallel_source_tasks(
            {"Reactome REST API": _reactome_rest},
            timeout_s=rest_timeout_s,
            caveats=caveats,
            label="Reactome pathway enrichment",
            max_workers=1,
        ).get("Reactome REST API") or {}
        for side in ("a", "b"):
            pathways = reactome.get(side) or []
            if pathways:
                mech[f"reactome_pathways_{side}"] = _merge_records(mech.get(f"reactome_pathways_{side}"), pathways, limit=10)
                mech[f"pathways_{side}"] = _append_unique(
                    mech.get(f"pathways_{side}"),
                    _pathway_names(pathways),
                    limit=24,
                )

    _ensure_enrichment_schema(mech)
    return mech


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


def _collect_public_clinical_reference(
    drugA: str,
    drugB: str,
    settings: Any,
    caveats: List[str],
) -> Dict[str, Any]:
    reference: Dict[str, Any] = {
        "rxnorm": {},
        "openfda_label": {},
        "dailymed": {},
        "fda_ddi_reference": {},
    }

    timeout_s = _env_float("INFERMED_CLINICAL_REF_TIMEOUT_S", 20.0)
    tasks: Dict[str, Callable[[], Any]] = {}

    if settings.enable_rxnorm:
        def _rxnorm_lookup() -> Dict[str, Any]:
            from src.retrieval.rxnorm_client import RxNormClient

            client = RxNormClient()
            return {
                "a": client.resolve_drug(drugA),
                "b": client.resolve_drug(drugB),
            }

        tasks["RxNorm/RxClass API"] = _rxnorm_lookup

    if settings.enable_openfda_label:
        def _openfda_label_lookup() -> Dict[str, Any]:
            from src.retrieval.openfda_label_client import OpenFDALabelClient

            client = OpenFDALabelClient()
            return {
                "a": client.get_label(drugA),
                "b": client.get_label(drugB),
            }

        tasks["openFDA Drug Label API"] = _openfda_label_lookup

    if settings.enable_dailymed:
        def _dailymed_lookup() -> Dict[str, Any]:
            from src.retrieval.dailymed_client import DailyMedClient

            client = DailyMedClient()
            return {
                "a": client.get_spl_metadata(drugA),
                "b": client.get_spl_metadata(drugB),
            }

        tasks["DailyMed SPL API"] = _dailymed_lookup

    def _fda_reference_lookup() -> Dict[str, Any]:
        from src.retrieval.fda_reference import match_fda_ddi_reference

        return {
            "a": match_fda_ddi_reference(drugA),
            "b": match_fda_ddi_reference(drugB),
        }

    tasks["FDA CYP/transporter reference"] = _fda_reference_lookup

    results = _run_parallel_source_tasks(
        tasks,
        timeout_s=timeout_s,
        caveats=caveats,
        label="Public clinical reference lookup",
        max_workers=4,
    )
    reference["rxnorm"] = results.get("RxNorm/RxClass API") or {}
    reference["openfda_label"] = results.get("openFDA Drug Label API") or {}
    reference["dailymed"] = results.get("DailyMed SPL API") or {}
    reference["fda_ddi_reference"] = results.get("FDA CYP/transporter reference") or {}

    return reference


def _clinical_reference_has_content(reference: Dict[str, Any]) -> bool:
    rxnorm = reference.get("rxnorm") or {}
    labels = reference.get("openfda_label") or {}
    dailymed = reference.get("dailymed") or {}
    fda_ddi = reference.get("fda_ddi_reference") or {}
    return bool(
        any((rxnorm.get(side) or {}).get("resolved") for side in ("a", "b"))
        or any((labels.get(side) or {}).get("found") for side in ("a", "b"))
        or any((dailymed.get(side) or {}).get("found") for side in ("a", "b"))
        or any((fda_ddi.get(side) or {}).get("matches") for side in ("a", "b"))
    )


def _rxnorm_id(reference: Dict[str, Any], side: str) -> Optional[str]:
    value = (((reference.get("rxnorm") or {}).get(side) or {}).get("rxcui"))
    if value:
        return str(value)
    label_rxcuis = (((reference.get("openfda_label") or {}).get(side) or {}).get("rxcui") or [])
    if label_rxcuis:
        return str(label_rxcuis[0])
    return None


def _research_seed_terms(mech: Dict[str, Any], *, limit: int = 16) -> List[str]:
    seeds: List[Any] = []
    for key in (
        "uniprot_ids_a",
        "uniprot_ids_b",
        "uniprot_targets_a",
        "uniprot_targets_b",
        "targets_a",
        "targets_b",
        "kegg_enzymes_a",
        "kegg_enzymes_b",
    ):
        seeds.extend(_iter_list(mech.get(key)))

    enzymes = mech.get("enzymes") or {}
    if isinstance(enzymes, Mapping):
        for side in ("a", "b"):
            roles = enzymes.get(side) or {}
            if not isinstance(roles, Mapping):
                continue
            for role in ("substrate", "inhibitor", "inducer"):
                seeds.extend(_iter_list(roles.get(role)))

    cleaned: List[str] = []
    for item in seeds:
        if isinstance(item, Mapping):
            for key in ("gene_names", "uniprot_id", "primaryAccession", "accession", "name", "protein_name", "enzyme_name"):
                value = item.get(key)
                if isinstance(value, list):
                    cleaned.extend(str(v) for v in value)
                elif value:
                    cleaned.append(str(value))
        else:
            cleaned.append(str(item))

    out: List[str] = []
    seen: set[str] = set()
    for text in cleaned:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text or len(text) < 2:
            continue
        # STRING/BioGRID work best with gene/protein identifiers; very long labels
        # are usually descriptions and make poor query seeds.
        if len(text) > 64:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _collect_api_research_enrichment(
    drugA: str,
    drugB: str,
    mech: Dict[str, Any],
    settings: Any,
    caveats: List[str],
) -> Dict[str, Any]:
    """Collect public API research context that supplements, but does not replace, PK/PD evidence."""
    enrichment: Dict[str, Any] = {
        "europe_pmc": {},
        "fda_pgx": {},
        "open_targets": {},
        "stringdb": {},
        "biogrid": {},
        "drugcentral": {},
        "local_bulk_sources": {
            "nci_almanac": {
                "enabled": bool(getattr(settings, "enable_nci_almanac", False)),
                "runtime": "DuckDB parquet layer when nci_almanac.parquet is present.",
                "scope": "Experimental oncology cell-line combination screen; hypothesis support only.",
            },
            "sider_nsides_offsides": {
                "enabled": bool(getattr(settings, "enable_sider_nsides_offsides", False)),
                "runtime": "DuckDB parquet layer when OFFSIDES/SIDER parquet files are present.",
                "scope": "Adverse-event and label side-effect context, not causal incidence.",
            },
        },
    }
    timeout_s = _env_float("INFERMED_RESEARCH_API_TIMEOUT_S", 24.0)
    seeds = _research_seed_terms(mech)
    tasks: Dict[str, Callable[[], Any]] = {}

    if getattr(settings, "enable_europe_pmc", False):
        def _europe_pmc_lookup() -> Dict[str, Any]:
            from src.retrieval.research_api_clients import EuropePMCClient

            return EuropePMCClient().search_interaction_literature(drugA, drugB, limit=5)

        tasks["Europe PMC REST API"] = _europe_pmc_lookup

    if getattr(settings, "enable_fda_pgx", False):
        def _fda_pgx_lookup() -> Dict[str, Any]:
            from src.retrieval.research_api_clients import FDAPGxClient

            return FDAPGxClient().get_pair_matches(drugA, drugB)

        tasks["FDA PGx biomarker pages"] = _fda_pgx_lookup

    if getattr(settings, "enable_open_targets", False):
        def _open_targets_lookup() -> Dict[str, Any]:
            from src.retrieval.research_api_clients import OpenTargetsClient

            client = OpenTargetsClient()
            protein_hits = {}
            for seed in seeds[:4]:
                protein_hits[seed] = client.search(seed, limit=3)
            return {
                "a": client.search(drugA, limit=5),
                "b": client.search(drugB, limit=5),
                "protein_seeds": protein_hits,
            }

        tasks["Open Targets GraphQL API"] = _open_targets_lookup

    if getattr(settings, "enable_stringdb", False) and seeds:
        def _string_lookup() -> Dict[str, Any]:
            from src.retrieval.research_api_clients import StringDBClient

            return StringDBClient(caller_identity=settings.string_caller_identity).get_network_summary(seeds[:12])

        tasks["STRING API"] = _string_lookup

    if getattr(settings, "enable_drugcentral", False):
        def _drugcentral_lookup() -> Dict[str, Any]:
            from src.retrieval.research_api_clients import DrugCentralClient

            client = DrugCentralClient()
            return {
                "a": client.get_drug_summary(drugA, target_limit=12),
                "b": client.get_drug_summary(drugB, target_limit=12),
            }

        tasks["DrugCentral DRS API"] = _drugcentral_lookup

    if getattr(settings, "enable_biogrid", False):
        def _biogrid_lookup() -> Dict[str, Any]:
            from src.retrieval.research_api_clients import BioGRIDClient

            return BioGRIDClient(access_key=settings.biogrid_access_key).get_interactions(seeds[:12])

        tasks["BioGRID REST API"] = _biogrid_lookup

    results = _run_parallel_source_tasks(
        tasks,
        timeout_s=timeout_s,
        caveats=caveats,
        label="API-first research enrichment",
        max_workers=5,
    )
    enrichment["europe_pmc"] = results.get("Europe PMC REST API") or {}
    enrichment["fda_pgx"] = results.get("FDA PGx biomarker pages") or {}
    enrichment["open_targets"] = results.get("Open Targets GraphQL API") or {}
    enrichment["stringdb"] = results.get("STRING API") or {}
    enrichment["biogrid"] = results.get("BioGRID REST API") or {}
    enrichment["drugcentral"] = results.get("DrugCentral DRS API") or {}
    enrichment["query_seeds"] = seeds
    return enrichment


def _research_enrichment_has_content(enrichment: Dict[str, Any]) -> bool:
    if not isinstance(enrichment, Mapping):
        return False
    europe = enrichment.get("europe_pmc") or {}
    pgx = enrichment.get("fda_pgx") or {}
    open_targets = enrichment.get("open_targets") or {}
    stringdb = enrichment.get("stringdb") or {}
    biogrid = enrichment.get("biogrid") or {}
    drugcentral = enrichment.get("drugcentral") or {}
    return bool(
        (isinstance(europe, Mapping) and europe.get("found"))
        or (isinstance(pgx, Mapping) and pgx.get("found"))
        or any((open_targets.get(side) or {}).get("found") for side in ("a", "b"))
        or (isinstance(stringdb, Mapping) and stringdb.get("found"))
        or (isinstance(biogrid, Mapping) and biogrid.get("found"))
        or any((drugcentral.get(side) or {}).get("found") for side in ("a", "b"))
    )


# ----------------- public pipeline -----------------
def retrieve_and_normalize(
    drugA: str,
    drugB: str,
    *,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/cache/openfda",
    topk_side_effects: int = 25,
    topk_faers: int = 10,
    topk_targets: int = 32,
    topk_pathways: int = 24,
) -> Dict[str, Any]:
    """Fetch raw evidence, normalize/synthesize PK/PD, and build the LLM context."""
    settings = get_settings()
    caveats: List[str] = []

    # 1) DuckDB init (idempotent)
    try:
        dq.init_duckdb_connection(
            parquet_dir,
            enable_drugbank=settings.enable_drugbank,
            enable_duckdb=settings.enable_duckdb,
            enable_nci_almanac=settings.enable_nci_almanac,
        )
    except Exception as e:
        caveats.append(f"DuckDB init failed: {e}")

    # NEW: instantiate DuckDBClient for method-based API
    db = dq.DuckDBClient(
        parquet_dir,
        enable_drugbank=settings.enable_drugbank,
        enable_duckdb=settings.enable_duckdb,
        enable_nci_almanac=settings.enable_nci_almanac,
    )

    a, b = drugA, drugB

    # 2) DuckDB: signals + DrugBank targets (PD fallback)
    prr_pair = None
    dili_a = dili_b = dict_a = dict_b = None
    diqt_a = diqt_b = None
    se_a_raw: List[str] = []
    se_b_raw: List[str] = []
    se_pair_raw: List[str] = []
    nci_almanac_rows: List[Dict[str, Any]] = []
    db_targets_a: List[str] = []
    db_targets_b: List[str] = []

    # Semantic search is useful enrichment, but demo/public-safe retrieval must still run without it.
    semantic_searcher = None
    use_semantic = False
    if settings.enable_semantic_search:
        try:
            semantic_searcher = get_semantic_searcher()
            use_semantic = bool(semantic_searcher and getattr(semantic_searcher, "model", None))
        except Exception as e:
            caveats.append(f"Semantic search unavailable: {e}")

    # Build drug index (required for semantic search)
    if use_semantic and semantic_searcher:
        # Get all unique drug names from database for indexing
        # This is a one-time operation, cached for subsequent queries
        try:
            drug_queries = []
            if hasattr(db, "has_view") and db.has_view("twosides"):
                drug_queries.extend([
                    "SELECT DISTINCT drug_a AS drug FROM twosides",
                    "SELECT DISTINCT drug_b AS drug FROM twosides",
                ])
            if hasattr(db, "has_view") and db.has_view("drugbank"):
                drug_queries.append("SELECT DISTINCT name_lower AS drug FROM drugbank")
            all_drugs_query = "\nUNION\n".join(drug_queries)
            all_drugs = [row[0] for row in db._con.execute(all_drugs_query).fetchall() if row[0]] if all_drugs_query else []
            if all_drugs and not semantic_searcher.build_drug_index(all_drugs, force_rebuild=False):
                LOG.warning("Failed to build drug index, continuing without semantic drug expansion")
        except Exception as e:
            caveats.append(f"Semantic drug index unavailable: {e}")
            use_semantic = False

    # Query expansion: Get synonyms and expand queries (REQUIRED for RAG system)
    synonyms_a = db.get_synonyms(a) if hasattr(db, 'get_synonyms') else []
    synonyms_b = db.get_synonyms(b) if hasattr(db, 'get_synonyms') else []

    # Expand drug queries. Fall back to lexical expansion if semantic search is unavailable.
    try:
        expanded_queries = create_expanded_query_context(
            a, b,
            synonyms_a=synonyms_a,
            synonyms_b=synonyms_b,
            semantic_searcher=semantic_searcher if use_semantic else None,
        )
    except Exception as e:
        caveats.append(f"Semantic query expansion unavailable: {e}")
        expanded_queries = {
            "original": {"drug_a": a, "drug_b": b},
            "expanded": expand_drug_pair_queries(a, b, synonyms_a=synonyms_a, synonyms_b=synonyms_b),
            "expansion_methods": {"synonyms": bool(synonyms_a or synonyms_b), "variations": True, "semantic": False},
        }

    expanded_a = expanded_queries["expanded"]["drug_a"]
    expanded_b = expanded_queries["expanded"]["drug_b"]

    # Use expanded queries for ALL retrieval operations

    try:
        # TwoSides side effects using HYBRID SEARCH and ADAPTIVE RETRIEVAL
        # Define keyword search function
        def keyword_search_side_effects(query: str, k: int) -> List[Tuple[str, float]]:
            """Keyword search for side effects."""
            results = db.get_side_effects(query, top_k=k) or []
            # Convert to (item, score) format with default score
            return [(se, 1.0) for se in results]

        # Define semantic search function for side effects
        def semantic_search_side_effects(query: str, k: int, threshold: float) -> List[Tuple[str, float]]:
            """Semantic search for side effects."""
            if not semantic_searcher or not semantic_searcher._initialized:
                return []
            # First get keyword results to build index if needed
            keyword_results = db.get_side_effects(query, top_k=k * 2) or []
            if keyword_results:
                semantic_searcher.build_side_effect_index(keyword_results, force_rebuild=False)
            # Search for similar side effects
            similar = semantic_searcher.search_similar_side_effects(query, top_k=k, threshold=threshold)
            return similar

        # Use adaptive hybrid search for drug A
        se_a_results, se_a_metadata = adaptive_hybrid_search(
            a,
            keyword_search_fn=lambda q, k: keyword_search_side_effects(q, k),
            semantic_search_fn=lambda q, k, t: semantic_search_side_effects(q, k, t),
            initial_k=topk_side_effects,
            min_relevance_threshold=0.3,
            max_k=topk_side_effects * 4
        )
        se_a_raw = [se for se, _ in se_a_results]

        # Use adaptive hybrid search for drug B
        se_b_results, se_b_metadata = adaptive_hybrid_search(
            b,
            keyword_search_fn=lambda q, k: keyword_search_side_effects(q, k),
            semantic_search_fn=lambda q, k, t: semantic_search_side_effects(q, k, t),
            initial_k=topk_side_effects,
            min_relevance_threshold=0.3,
            max_k=topk_side_effects * 4
        )
        se_b_raw = [se for se, _ in se_b_results]

        # For pair, use hybrid search with expanded terms
        def keyword_search_pair(term_a: str, term_b: str, k: int) -> List[Tuple[str, float]]:
            """Keyword search for pair side effects."""
            results = db.get_side_effects(term_a, term_b, top_k=k) or []
            return [(se, 1.0) for se in results]

        # Try original pair first
        pair_keyword_results = keyword_search_pair(a, b, topk_side_effects * 2)
        if pair_keyword_results:
            se_pair_raw = [se for se, _ in pair_keyword_results]
        else:
            # Try expanded term combinations
            se_pair_raw = []
            for expanded_term_a in expanded_a[:3]:
                for expanded_term_b in expanded_b[:3]:
                    if expanded_term_a == a and expanded_term_b == b:
                        continue
                    results = keyword_search_pair(expanded_term_a, expanded_term_b, topk_side_effects * 2)
                    if results:
                        se_pair_raw.extend([se for se, _ in results])
                        if len(se_pair_raw) >= topk_side_effects * 2:
                            break
                if se_pair_raw:
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
        nci_almanac_rows = db.get_nci_almanac_pair(a, b, top_k=12) if hasattr(db, "get_nci_almanac_pair") else []

        # DrugBank targets (PD fallback) - use query expansion
        db_targets_a = []
        db_targets_b = []
        # Try expanded terms for targets
        for expanded_term_a in expanded_a:
            targets = db.get_drug_targets(expanded_term_a) or []
            if targets:
                db_targets_a = targets
                break

        for expanded_term_b in expanded_b:
            targets = db.get_drug_targets(expanded_term_b) or []
            if targets:
                db_targets_b = targets
                break

        # Fallback to original if no expanded terms found targets
        if not db_targets_a:
            db_targets_a = db.get_drug_targets(a) or []
        if not db_targets_b:
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
    mech = _enrich_mechanistic_with_public_rest(a, b, mech, settings, caveats)

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

    # 5) FAERS via OpenFDA (cached) - use query expansion
    faers_a: List[tuple] = []
    faers_b: List[tuple] = []
    faers_combo: List[tuple] = []
    if settings.enable_openfda:
        try:
            ofda = OpenFDAClient(cache_dir=openfda_cache)
            # Try expanded terms for FAERS
            for expanded_term_a in expanded_a:
                reactions = ofda.get_top_reactions(expanded_term_a, top_k=topk_faers * 2)
                if reactions:
                    faers_a = reactions
                    break
            if not faers_a:
                faers_a = ofda.get_top_reactions(a, top_k=topk_faers)

            for expanded_term_b in expanded_b:
                reactions = ofda.get_top_reactions(expanded_term_b, top_k=topk_faers * 2)
                if reactions:
                    faers_b = reactions
                    break
            if not faers_b:
                faers_b = ofda.get_top_reactions(b, top_k=topk_faers)

            # For combo, try original pair first, then expanded combinations
            faers_combo = ofda.get_combination_reactions(a, b, top_k=topk_faers * 2)
            if not faers_combo:
                # Try expanded term combinations
                for expanded_term_a in expanded_a[:3]:  # Limit to top 3
                    for expanded_term_b in expanded_b[:3]:
                        if expanded_term_a == a and expanded_term_b == b:
                            continue  # Already tried
                        reactions = ofda.get_combination_reactions(expanded_term_a, expanded_term_b, top_k=topk_faers * 2)
                        if reactions:
                            faers_combo.extend(reactions)
                            if len(faers_combo) >= topk_faers * 2:
                                break
                    if faers_combo:
                        break
        except Exception as e:
            caveats.append(f"OpenFDA retrieval failed: {e}")
    else:
        caveats.append("OpenFDA retrieval disabled by config.")

    # Enforce FAERS top-K even if client returns more
    faers_a = (faers_a or [])[:topk_faers]
    faers_b = (faers_b or [])[:topk_faers]
    faers_combo = (faers_combo or [])[:topk_faers]

    # Normalize FAERS to JSON-stable lists-of-lists so cache equals fresh
    faers_a = _to_pairs_list(faers_a)
    faers_b = _to_pairs_list(faers_b)
    faers_combo = _to_pairs_list(faers_combo)

    # 5b) Public clinical authority and identity context.
    clinical_reference = _collect_public_clinical_reference(a, b, settings, caveats)

    # 5c) API-first research context. This supplements source review and
    # hypothesis generation; it does not alter deterministic PK/PD scoring.
    research_enrichment = _collect_api_research_enrichment(a, b, mech, settings, caveats)

    # 6) Build normalized context (matches llm_interface expectations)
    ql_contrib = _bool_qlever_contributed(mech)

    # Build caveats with explicit fallback note if QLever RAW contributed nothing
    caveats_agg = list(dict.fromkeys((qlev.get("caveats") or []) + caveats))
    if not ql_raw_contrib:
        if settings.enable_qlever:
            caveats_agg.append("QLever mechanistic unavailable; using available non-QLever evidence.")
        else:
            caveats_agg.append("QLever RDF disabled for NVIDIA demo runtime.")

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
    source_status = [asdict(status) for status in get_source_status(settings)]

    duckdb_sources: List[str] = []
    if hasattr(db, "get_available_sources"):
        available_sources = db.get_available_sources()
        duckdb_label_by_key = {
            "twosides": "TwoSides",
            "offsides": "OFFSIDES",
            "sider_label_side_effects": "SIDER",
            "nci_almanac": "NCI-ALMANAC",
            "dilirank": "DILIrank",
            "dictrank": "DICTRank",
            "diqt": "DIQT",
            "drugbank": "DrugBank local dataset",
        }
        duckdb_sources = [label for key, label in duckdb_label_by_key.items() if available_sources.get(key)]

    api_sources = []
    if settings.enable_pubchem_rest:
        api_sources.append("PubChem REST API")
    if settings.enable_uniprot:
        api_sources.append("UniProt REST API")
    if settings.enable_kegg:
        api_sources.append("KEGG REST API")
    if settings.enable_reactome:
        api_sources.append("Reactome REST API")
    if settings.enable_chembl:
        api_sources.append("ChEMBL REST API")
    if settings.enable_openfda_label:
        api_sources.append("openFDA Drug Label API")
    if settings.enable_dailymed:
        api_sources.append("DailyMed SPL API")
    if settings.enable_rxnorm:
        api_sources.append("RxNorm/RxClass API")
    if (clinical_reference.get("fda_ddi_reference") or {}).get("a", {}).get("matches") or (
        clinical_reference.get("fda_ddi_reference") or {}
    ).get("b", {}).get("matches"):
        api_sources.append("FDA CYP/transporter reference")
    if settings.enable_fda_pgx:
        api_sources.append("FDA PGx biomarker pages")
    if settings.enable_europe_pmc:
        api_sources.append("Europe PMC REST API")
    if settings.enable_open_targets:
        api_sources.append("Open Targets GraphQL API")
    if settings.enable_stringdb:
        api_sources.append("STRING API")
    if settings.enable_drugcentral:
        api_sources.append("DrugCentral DRS API")
    if settings.enable_biogrid:
        api_sources.append("BioGRID REST API")

    ids_a = dict(qlev.get("ids_a", {}) or mech.get("ids_a", {}) or {})
    ids_b = dict(qlev.get("ids_b", {}) or mech.get("ids_b", {}) or {})
    rxnorm_a = _rxnorm_id(clinical_reference, "a")
    rxnorm_b = _rxnorm_id(clinical_reference, "b")
    if rxnorm_a:
        ids_a.setdefault("rxcui", rxnorm_a)
    if rxnorm_b:
        ids_b.setdefault("rxcui", rxnorm_b)

    context: Dict[str, Any] = {
        "drugs": {
            "a": {"name": a, "synonyms": qlev.get("synonyms_a", []) or mech.get("synonyms_a", []), "ids": ids_a},
            "b": {"name": b, "synonyms": qlev.get("synonyms_b", []) or mech.get("synonyms_b", []), "ids": ids_b},
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
                "nci_almanac": nci_almanac_rows,
            },
            "mechanistic": mech,
            "faers": {
                "top_reactions_a": faers_a,
                "top_reactions_b": faers_b,
                "combo_reactions": faers_combo,
            },
            "clinical_reference": clinical_reference,
            "research_enrichment": research_enrichment,
        },
        "caveats": caveats_agg,
        "pkpd": pkpd,
        "source_status": source_status,
        "sources": {
            "duckdb": duckdb_sources,
            "qlever": ["PubChem RDF via QLever"] if (settings.enable_qlever and ql_raw_contrib) else [],
            "openfda": ["FAERS via OpenFDA (cached)"] if settings.enable_openfda else [],
            "apis": api_sources,
            "canonical": ["Canonical PK/PD Dictionary"] if has_canonical else [],
            "semantic": ["Semantic Search (Embeddings)"] if use_semantic else [],
            "query_expansion": ["Query Expansion (Synonyms, Variations" + (", Semantic Similarity" if use_semantic else "") + ")"],
            "hybrid_search": ["Hybrid Search (Keyword + Semantic)"] if use_semantic else ["Keyword Search"],
            "adaptive_retrieval": ["Adaptive Retrieval (Dynamic Top-K)"],
        },
        "meta": {
            "drug_pair": f"{drugA}|{drugB}",
            "pair_key": _pair_key(drugA, drugB),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "version": VERSION,
            "data_mode": settings.data_mode,
            "qlever_contributed_raw": ql_raw_contrib,
            "qlever_contributed": ql_contrib,
            "clinical_reference_contributed": _clinical_reference_has_content(clinical_reference),
            "research_enrichment_contributed": _research_enrichment_has_content(research_enrichment),
        },
    }

    # Apply reranking when available. The demo path must not require embedding models.
    reranker = None
    if settings.enable_reranking:
        try:
            reranker = get_reranker()
        except Exception as e:
            caveats_agg.append(f"Reranking unavailable: {e}")

    if reranker is not None and getattr(reranker, "_initialized", False):
        # Rerank side effects by query relevance
        query_text = f"{a} and {b} interaction"
        if side_effects_a and scored_se_a:
            reranked_se_a = reranker.rerank_with_scores(
                query_text,
                scored_se_a[:topk_side_effects * 2],
                top_k=topk_side_effects,
                combine_with_original=True,
                original_weight=0.4,
                rerank_weight=0.6
            )
            side_effects_a = [se for se, _ in reranked_se_a]
            context["signals"]["tabular"]["side_effects_a"] = side_effects_a

        if side_effects_b and scored_se_b:
            reranked_se_b = reranker.rerank_with_scores(
                query_text,
                scored_se_b[:topk_side_effects * 2],
                top_k=topk_side_effects,
                combine_with_original=True,
                original_weight=0.4,
                rerank_weight=0.6
            )
            side_effects_b = [se for se, _ in reranked_se_b]
            context["signals"]["tabular"]["side_effects_b"] = side_effects_b

        # Also rerank targets and pathways if available
        mech_targets_a = mech.get("targets_a", [])
        mech_targets_b = mech.get("targets_b", [])
        if mech_targets_a:
            target_texts = [f"{a} targets {t}" for t in mech_targets_a]
            reranked_targets_a = reranker.rerank(
                f"{a} drug targets",
                target_texts,
                top_k=topk_targets
            )
            context["signals"]["mechanistic"]["targets_a"] = [t.split()[-1] for t, _ in reranked_targets_a]

        if mech_targets_b:
            target_texts = [f"{b} targets {t}" for t in mech_targets_b]
            reranked_targets_b = reranker.rerank(
                f"{b} drug targets",
                target_texts,
                top_k=topk_targets
            )
            context["signals"]["mechanistic"]["targets_b"] = [t.split()[-1] for t, _ in reranked_targets_b]
        context["sources"]["reranking"] = ["Re-ranking (Cross-Encoder)"]

    # Apply query-to-context filtering to remove low-relevance items
    query_text = f"{a} + {b}"
    filtered_context, filter_metadata = filter_context_by_relevance(
        context,
        query_text,
        query_context_se,
        min_relevance=0.0,  # Preserve retrieved evidence for source-traced demo output.
        preserve_structure=True
    )

    # Add filtering metadata to context
    if filter_metadata.get("filter_ratio", 0) > 0:
        filtered_context["meta"]["context_filtered"] = True
        filtered_context["meta"]["filter_ratio"] = filter_metadata["filter_ratio"]
        filtered_context["meta"]["filtered_sections"] = filter_metadata.get("filtered_sections", [])

    return filtered_context


def get_context_cached(
    drugA: str,
    drugB: str,
    *,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/cache/openfda",
    force_refresh: bool = False,
    topk_side_effects: int = 25,
    topk_faers: int = 10,
    topk_targets: int = 32,
    topk_pathways: int = 24,
) -> Tuple[Dict[str, Any], str]:
    """
    Cache the normalized context (structured JSON) because retrieval is slow.
    Cache files use readable drug-pair names. Lookup also checks the reverse
    pair order and the old opaque hash key so existing caches continue to work.
    """
    key = _context_cache_key(drugA, drugB)
    path = _ctx_path(key)
    if not force_refresh:
        for candidate in _context_cache_candidates(drugA, drugB):
            candidate_path = _ctx_path(candidate)
            if not os.path.exists(candidate_path):
                continue
            with open(candidate_path, "r", encoding="utf-8") as f:
                cached_ctx = json.load(f)
            if not _context_cache_version_ok(cached_ctx):
                continue
            if candidate != key:
                if candidate == _legacy_context_cache_key(drugA, drugB):
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(cached_ctx, f, ensure_ascii=False, indent=2)
                    try:
                        os.remove(candidate_path)
                    except OSError:
                        pass
                    return cached_ctx, key
                return cached_ctx, candidate
            return cached_ctx, key
        cached_ctx, found_key = _find_context_cache_by_pair(drugA, drugB)
        if cached_ctx is not None:
            if found_key != key:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cached_ctx, f, ensure_ascii=False, indent=2)
            return cached_ctx, key

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
    model_name: Optional[str] = None,
    stream: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Backward-compatible wrapper. LLM responses are intentionally not cached
    because response text can become stale while retrieval evidence changes.
    """
    return generate_response(context, mode, seed=seed, temperature=temperature, model_name=model_name, stream=stream)


def run_rag(
    drugA: str,
    drugB: str,
    *,
    mode: str = "Patient",
    seed: Optional[int] = 42,
    temperature: float = 0.2,
    parquet_dir: str = "data/duckdb",
    openfda_cache: str = "data/cache/openfda",
    history: Optional[List[Dict[str, str]]] = None,
    use_cache_context: bool = True,
    use_cache_response: bool = False,
    topk_side_effects: int = 25,
    topk_faers: int = 10,
    topk_targets: int = 32,
    topk_pathways: int = 24,
    model_name: Optional[str] = None,  # Override default model
    stream: Optional[bool] = None,
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

    answer = generate_response(
        context,
        mode,
        seed=seed,
        temperature=temperature,
        history=history or [],
        model_name=model_name,
        stream=stream,
    )

    # Track retrieved items for potential feedback (optional)
    retrieved_items = []
    if context.get("signals"):
        signals = context["signals"]
        if "tabular" in signals:
            retrieved_items.extend(signals["tabular"].get("side_effects_a", []))
            retrieved_items.extend(signals["tabular"].get("side_effects_b", []))
        if "mechanistic" in signals:
            retrieved_items.extend(signals["mechanistic"].get("targets_a", []))
            retrieved_items.extend(signals["mechanistic"].get("targets_b", []))

    # Store retrieved items in answer metadata for feedback tracking
    if "meta" not in answer:
        answer["meta"] = {}
    answer["meta"]["retrieved_items"] = list(set(retrieved_items))  # Deduplicate

    return {"context": context, "answer": answer}


def record_feedback(
    drugA: str,
    drugB: str,
    response_quality: str,
    user_rating: Optional[float] = None,
    context: Optional[Dict[str, Any]] = None,
    mode: Optional[str] = None,
):
    """
    Record feedback on a query-response pair for learning.

    Args:
        drugA: First drug name
        drugB: Second drug name
        response_quality: Quality assessment ("good", "bad", "neutral")
        user_rating: Optional numeric rating (0-1)
        context: Optional context dictionary
        mode: Audience/workflow mode, when feedback is collected from the UI
    """
    try:
        tracker = get_feedback_tracker()

        # Extract retrieved items from context if provided
        retrieved_items = []
        if context and "signals" in context:
            signals = context["signals"]
            if "tabular" in signals:
                retrieved_items.extend(signals["tabular"].get("side_effects_a", []))
                retrieved_items.extend(signals["tabular"].get("side_effects_b", []))
            if "mechanistic" in signals:
                retrieved_items.extend(signals["mechanistic"].get("targets_a", []))
                retrieved_items.extend(signals["mechanistic"].get("targets_b", []))

        query = f"{drugA} + {drugB}" + (f" [{mode}]" if mode else "")
        tracker.record_feedback(
            query,
            retrieved_items,
            response_quality,
            user_rating=user_rating,
            context=_feedback_context_snapshot(context, mode=mode) if context else None,
        )
    except Exception as e:
        LOG.warning(f"Failed to record feedback: {e}")


def _feedback_context_snapshot(context: Dict[str, Any], *, mode: Optional[str] = None) -> Dict[str, Any]:
    """Keep feedback useful without storing the full retrieved context blob."""
    signals = context.get("signals", {}) or {}
    tabular = signals.get("tabular", {}) or {}
    faers = signals.get("faers", {}) or {}
    mechanistic = signals.get("mechanistic", {}) or {}
    clinical_reference = signals.get("clinical_reference", {}) or {}
    research_enrichment = signals.get("research_enrichment", {}) or {}
    pkpd = context.get("pkpd", {}) or {}
    meta = context.get("meta", {}) or {}

    return {
        "mode": mode,
        "drugs": context.get("drugs", {}),
        "meta": {
            "pair_key": meta.get("pair_key"),
            "version": meta.get("version"),
            "data_mode": meta.get("data_mode"),
        },
        "sources": context.get("sources", {}),
        "source_status": context.get("source_status", []),
        "risk": {
            "interaction_score": tabular.get("interaction_score"),
            "dili_a": tabular.get("dili_a"),
            "dili_b": tabular.get("dili_b"),
            "dict_a": tabular.get("dict_a"),
            "dict_b": tabular.get("dict_b"),
            "diqt_a": tabular.get("diqt_a"),
            "diqt_b": tabular.get("diqt_b"),
        },
        "pkpd": {
            "pk_summary": pkpd.get("pk_summary"),
            "pd_summary": pkpd.get("pd_summary"),
            "pk_detail": pkpd.get("pk_detail"),
            "pd_detail": pkpd.get("pd_detail"),
        },
        "evidence_preview": {
            "side_effects_a": tabular.get("side_effects_a", [])[:10],
            "side_effects_b": tabular.get("side_effects_b", [])[:10],
            "side_effects_pair": tabular.get("side_effects_pair", [])[:10],
            "faers_a": faers.get("top_reactions_a", [])[:10],
            "faers_b": faers.get("top_reactions_b", [])[:10],
            "faers_combo": faers.get("combo_reactions", [])[:10],
            "targets_a": mechanistic.get("targets_a", [])[:10],
            "targets_b": mechanistic.get("targets_b", [])[:10],
            "pathways_a": mechanistic.get("pathways_a", [])[:10],
            "pathways_b": mechanistic.get("pathways_b", [])[:10],
            "clinical_reference": {
                "rxnorm": clinical_reference.get("rxnorm", {}),
                "openfda_label_found": {
                    side: bool(((clinical_reference.get("openfda_label", {}) or {}).get(side) or {}).get("found"))
                    for side in ("a", "b")
                },
                "dailymed_found": {
                    side: bool(((clinical_reference.get("dailymed", {}) or {}).get(side) or {}).get("found"))
                    for side in ("a", "b")
                },
            },
            "research_enrichment": {
                "europe_pmc_count": len(((research_enrichment.get("europe_pmc") or {}).get("articles") or []))
                if isinstance(research_enrichment, Mapping)
                else 0,
                "fda_pgx_found": bool((research_enrichment.get("fda_pgx") or {}).get("found"))
                if isinstance(research_enrichment, Mapping)
                else False,
                "stringdb_found": bool((research_enrichment.get("stringdb") or {}).get("found"))
                if isinstance(research_enrichment, Mapping)
                else False,
                "opentargets_found": bool(
                    isinstance(research_enrichment, Mapping)
                    and any(((research_enrichment.get("open_targets") or {}).get(side) or {}).get("found") for side in ("a", "b"))
                ),
            },
        },
        "caveats": (context.get("caveats") or [])[:10],
    }


# ----------------- small cache helpers (optional) -----------------
def clear_context_cache(drugA: str, drugB: str) -> bool:
    """Delete cached contexts for a pair in either order. Returns True if removed."""
    removed = False
    for key in _context_cache_candidates(drugA, drugB):
        path = _ctx_path(key)
        if os.path.exists(path):
            os.remove(path)
            removed = True
    pair_key = _pair_key(drugA, drugB)
    if os.path.isdir(CTX_DIR):
        for name in os.listdir(CTX_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(CTX_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    meta = (json.load(f).get("meta") or {})
            except Exception:
                continue
            if meta.get("pair_key") == pair_key:
                os.remove(path)
                removed = True
    return removed


def load_pkpd_from_cache(drugA: str, drugB: str) -> Optional[Dict[str, Any]]:
    """Convenience accessor to load only the PK/PD block from cache, if present."""
    for key in _context_cache_candidates(drugA, drugB):
        path = _ctx_path(key)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            ctx = json.load(f)
        if _context_cache_version_ok(ctx):
            return ctx.get("pkpd")
    cached_ctx, _ = _find_context_cache_by_pair(drugA, drugB)
    if cached_ctx is not None:
        return cached_ctx.get("pkpd")
    return None
