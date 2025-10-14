# src/utils/pkpd_utils.py
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set, Tuple
import re
import unicodedata

__all__ = [
    "canonicalize_enzyme",
    "canonicalize_list",
    "extract_pk_roles",
    "detect_pk_overlaps",
    "pd_overlap",
    "summarize_pkpd_risk",
    "topk_faers",
    "synthesize_mechanistic",
]

# --------------------------------------------------------------------------------------
# Normalization utilities
# --------------------------------------------------------------------------------------

# Extendable synonym table for common CYPs (lowercased, whitespace-normalized)
_CYP_SYNONYMS: Dict[str, Set[str]] = {
    "cyp3a4": {"cyp3a4", "cytochrome p450 3a4", "cyp 3a4", "p450 3a4"},
    "cyp2c9": {"cyp2c9", "cytochrome p450 2c9", "cyp 2c9", "p450 2c9"},
    "cyp2d6": {"cyp2d6", "cytochrome p450 2d6", "cyp 2d6", "p450 2d6"},
    "cyp1a2": {"cyp1a2", "cytochrome p450 1a2", "cyp 1a2", "p450 1a2"},
    "cyp2c19": {"cyp2c19", "cytochrome p450 2c19", "cyp 2c19", "p450 2c19"},
}
_CANON_BY_ALIAS: Dict[str, str] = {
    alias: canon for canon, aliases in _CYP_SYNONYMS.items() for alias in aliases
}


def _norm_text(s: str) -> str:
    """Lowercase, trim, collapse whitespace, Unicode NFKC normalize."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def canonicalize_enzyme(name: str) -> str:
    """
    Map an enzyme name/synonym to a canonical token when known (e.g., 'CYP3A4' → 'cyp3a4').
    Unknown strings are normalized and returned as-is.
    """
    a = _norm_text(name)
    return _CANON_BY_ALIAS.get(a, a)


def canonicalize_list(values: Iterable[str], *, topk: int | None = None) -> List[str]:
    """
    Normalize a list of strings (dedup, order preserved by first occurrence).
    Optionally cap to topk items.
    """
    seen: Set[str] = set()
    out: List[str] = []
    for v in values or []:
        c = _norm_text(v)
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
        if topk and len(out) >= topk:
            break
    return out


# --------------------------------------------------------------------------------------
# PK roles & overlaps
# --------------------------------------------------------------------------------------

def extract_pk_roles(mech: Dict[str, Any]) -> Dict[str, Dict[str, Set[str]]]:
    """
    From a mechanistic dict shaped like:
      mech['enzymes'] = {
        'a': {'substrate': [...], 'inhibitor': [...], 'inducer': [...]},
        'b': {'substrate': [...], 'inhibitor': [...], 'inducer': [...]},
      }
    produce canonical enzyme sets per role, per side.
    """
    ez = (mech or {}).get("enzymes", {}) or {}
    roles: Dict[str, Dict[str, Set[str]]] = {}
    for side in ("a", "b"):
        side_map = ez.get(side, {}) or {}
        roles[side] = {
            "substrate": {canonicalize_enzyme(x) for x in side_map.get("substrate", [])},
            "inhibitor": {canonicalize_enzyme(x) for x in side_map.get("inhibitor", [])},
            "inducer":   {canonicalize_enzyme(x) for x in side_map.get("inducer",   [])},
        }
    return roles


def detect_pk_overlaps(roles: Dict[str, Dict[str, Set[str]]]) -> Dict[str, Set[str]]:
    """
    Detect plausible PK interactions:
      - inhibition: A_sub ∧ B_inh  OR  B_sub ∧ A_inh
      - induction:  A_sub ∧ B_ind  OR  B_sub ∧ A_ind
      - shared_substrate: A_sub ∧ B_sub
    """
    a = roles.get("a", {})
    b = roles.get("b", {})
    inhib = (a.get("substrate", set()) & b.get("inhibitor", set())) | (b.get("substrate", set()) & a.get("inhibitor", set()))
    induc = (a.get("substrate", set()) & b.get("inducer", set()))   | (b.get("substrate", set()) & a.get("inducer", set()))
    shared = a.get("substrate", set()) & b.get("substrate", set())
    return {"inhibition": inhib, "induction": induc, "shared_substrate": shared}


# --------------------------------------------------------------------------------------
# PD overlap
# --------------------------------------------------------------------------------------

def pd_overlap(mech: Dict[str, Any], *, target_topk: int = 32, path_topk: int = 24) -> Dict[str, Any]:
    """
    Compute simple PD overlap from targets/pathways.
    Returns:
      {
        "overlap_targets": [...],
        "overlap_pathways": [...],
        "pd_score": float in [0,1]
      }
    """
    ta = set(canonicalize_list((mech or {}).get("targets_a", []), topk=target_topk))
    tb = set(canonicalize_list((mech or {}).get("targets_b", []), topk=target_topk))
    pa = set(canonicalize_list((mech or {}).get("pathways_a", []), topk=path_topk))
    pb = set(canonicalize_list((mech or {}).get("pathways_b", []), topk=path_topk))
    common_targets = sorted(ta & tb)
    common_paths = sorted(pa & pb)
    # A tiny heuristic score: equal weight targets and pathways, saturate at 10 each.
    score = min(1.0, 0.5 * (len(common_targets) / 10.0) + 0.5 * (len(common_paths) / 10.0))
    return {"overlap_targets": common_targets, "overlap_pathways": common_paths, "pd_score": round(score, 3)}


# --------------------------------------------------------------------------------------
# Synthesis (compact summaries for LLM)
# --------------------------------------------------------------------------------------

def summarize_pkpd_risk(drugA: str, drugB: str, mech: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate compact PK/PD summaries + details to be surfaced in the prompt.
    """
    roles = extract_pk_roles(mech)
    overlaps = detect_pk_overlaps(roles)
    pd = pd_overlap(mech)

    pk_flags: List[str] = []
    if overlaps["inhibition"]:
        pk_flags.append("Potential ↑ exposure via inhibition at " + ", ".join(sorted(overlaps["inhibition"])))
    if overlaps["induction"]:
        pk_flags.append("Potential ↓ exposure via induction at " + ", ".join(sorted(overlaps["induction"])))
    if overlaps["shared_substrate"]:
        pk_flags.append("Both are substrates of " + ", ".join(sorted(overlaps["shared_substrate"])) + " (competition possible)")

    pd_flags: List[str] = []
    if pd["overlap_targets"]:
        pd_flags.append("Overlapping targets: " + ", ".join(pd["overlap_targets"][:8]))
    if pd["overlap_pathways"]:
        pd_flags.append("Common pathways: " + ", ".join(pd["overlap_pathways"][:8]))

    return {
        "pk_summary": "; ".join(pk_flags) if pk_flags else "No strong PK overlap detected",
        "pd_summary": "; ".join(pd_flags) if pd_flags else "No obvious PD overlap",
        "pk_detail": {"roles": roles, "overlaps": overlaps},
        "pd_detail": pd,
    }


# --------------------------------------------------------------------------------------
# FAERS compact formatting
# --------------------------------------------------------------------------------------

def topk_faers(faers: Dict[str, Any], k: int = 5) -> Dict[str, str]:
    """
    Convert FAERS tuples to short 'term (n=count)' strings with consistent
    'No evidence from FAERS.' when empty. Safely handles malformed rows.
    """
    def coerce_pairs(items: Any) -> List[Tuple[str, int]]:
        out: List[Tuple[str, int]] = []
        for it in (items or []):
            try:
                t, c = it
                out.append((str(t), int(c)))
            except Exception:
                # skip malformed rows silently
                continue
        return out

    def fmt(items: Any) -> str:
        pairs = coerce_pairs(items)
        if not pairs:
            return "No evidence from FAERS."
        return ", ".join([f"{t} (n={c})" for t, c in pairs[:k]])

    return {
        "a": fmt(faers.get("top_reactions_a")),
        "b": fmt(faers.get("top_reactions_b")),
        "combo": fmt(faers.get("combo_reactions")),
    }


# --------------------------------------------------------------------------------------
# Glue helper: safely combine QLever + DuckDB PD targets
# --------------------------------------------------------------------------------------

def synthesize_mechanistic(
    qlever_mech: Dict[str, Any] | None,
    *,
    fallback_targets_a: Iterable[str] | None = None,
    fallback_targets_b: Iterable[str] | None = None,
) -> Dict[str, Any]:
    """
    Merge mechanistic info, using DuckDB DrugBank targets as PD fallback when QLever
    doesn't provide targets. Keeps shape required by the context schema.
    Ensures enzymes/targets/pathways are normalized.
    """
    q = qlever_mech or {}

    # --- Enzymes: normalize per role per side ---
    raw_ez = q.get("enzymes", {}) or {"a": {}, "b": {}}
    def canon_roles(side_map: Dict[str, Any]) -> Dict[str, List[str]]:
        return {
            "substrate": [canonicalize_enzyme(x) for x in (side_map.get("substrate") or [])],
            "inhibitor": [canonicalize_enzyme(x) for x in (side_map.get("inhibitor") or [])],
            "inducer":   [canonicalize_enzyme(x) for x in (side_map.get("inducer")   or [])],
        }
    enzymes = {
        "a": canon_roles(raw_ez.get("a", {}) or {}),
        "b": canon_roles(raw_ez.get("b", {}) or {}),
    }

    # --- Targets / pathways: normalize whether from QLever or fallback ---
    t_a = q.get("targets_a")
    t_b = q.get("targets_b")
    p_a = q.get("pathways_a")
    p_b = q.get("pathways_b")
    cp  = q.get("common_pathways")

    targets_a = canonicalize_list(t_a if t_a else (fallback_targets_a or []))
    targets_b = canonicalize_list(t_b if t_b else (fallback_targets_b or []))
    pathways_a = canonicalize_list(p_a or [])
    pathways_b = canonicalize_list(p_b or [])
    common_pathways = canonicalize_list(cp or [])

    mech: Dict[str, Any] = {
        "enzymes": enzymes,
        "targets_a": targets_a,
        "targets_b": targets_b,
        "pathways_a": pathways_a,
        "pathways_b": pathways_b,
        "common_pathways": common_pathways,
    }
    return mech

