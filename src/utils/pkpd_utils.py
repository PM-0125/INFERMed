# src/utils/pkpd_utils.py
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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

# Extendable synonym table for common CYPs & key Phase II/transporters (lowercased, whitespace/hyphen-normalized)
_CYP_SYNONYMS: Dict[str, Set[str]] = {
    "cyp3a4":  {"cyp3a4", "cytochrome p450 3a4", "cyp 3a4", "p450 3a4"},
    "cyp3a5":  {"cyp3a5", "cytochrome p450 3a5", "cyp 3a5", "p450 3a5"},
    "cyp2c9":  {"cyp2c9", "cytochrome p450 2c9", "cyp 2c9", "p450 2c9"},
    "cyp2d6":  {"cyp2d6", "cytochrome p450 2d6", "cyp 2d6", "p450 2d6"},
    "cyp1a2":  {"cyp1a2", "cytochrome p450 1a2", "cyp 1a2", "p450 1a2"},
    "cyp2c19": {"cyp2c19","cytochrome p450 2c19","cyp 2c19","p450 2c19"},
    # UGT (phase II)
    "ugt1a1":  {"ugt1a1","ugt 1a1","uridine diphospho glucuronosyltransferase 1a1"},
    # Transporters
    "abcb1":   {"abcb1","p gp","p-gp","pgp","p glycoprotein","p-glycoprotein","mdr1"},
    "slco1b1": {"slco1b1","oatp1b1","oatp 1b1"},
    "abcg2":   {"abcg2","bcrp","breast cancer resistance protein"},
}
# Flatten alias→canonical map
_CANON_BY_ALIAS: Dict[str, str] = {
    alias: canon for canon, aliases in _CYP_SYNONYMS.items() for alias in aliases
}

_HYPHEN_SPACE = re.compile(r"[\s\-]+")

def _norm_text(s: str) -> str:
    """Lowercase, trim, collapse whitespace/hyphens, Unicode NFKC normalize."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower().strip()
    s = _HYPHEN_SPACE.sub(" ", s)
    return s

def canonicalize_enzyme(name: str) -> str:
    """
    Map an enzyme/transporter name/synonym to a canonical token (e.g., 'P-gp' → 'abcb1').
    Unknown strings are normalized and returned as-is.
    """
    a = _norm_text(name)
    return _CANON_BY_ALIAS.get(a, a)

def _stringify_item(v: Any) -> str:
    """
    Convert item to string for canonicalization.
    - dict -> use 'label' if present else 'uri' else ''
    - other -> str(v)
    """
    if isinstance(v, dict):
        return str(v.get("label") or v.get("uri") or "")
    return str(v)

def canonicalize_list(values: Iterable[Any], *, topk: int | None = None) -> List[str]:
    """
    Normalize a list of strings (or dicts with 'label'/'uri'):
      - stringify (dict label→uri→''),
      - lowercase + trim + collapse spaces/hyphens,
      - deduplicate preserving first occurrence,
      - optional topk cap.
    """
    seen: Set[str] = set()
    out: List[str] = []
    for v in values or []:
        c = _norm_text(_stringify_item(v))
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
    produce canonical enzyme/transporters sets per role, per side.
    """
    ez = (mech or {}).get("enzymes", {}) or {}
    roles: Dict[str, Dict[str, Set[str]]] = {}
    for side in ("a", "b"):
        side_map = ez.get(side, {}) or {}
        roles[side] = {
            "substrate": {canonicalize_enzyme(_stringify_item(x)) for x in side_map.get("substrate", [])},
            "inhibitor": {canonicalize_enzyme(_stringify_item(x)) for x in side_map.get("inhibitor", [])},
            "inducer":   {canonicalize_enzyme(_stringify_item(x)) for x in side_map.get("inducer",   [])},
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
    # Heuristic score: equal weight targets and pathways, saturate at 10 each.
    score = min(1.0, 0.5 * (len(common_targets) / 10.0) + 0.5 * (len(common_paths) / 10.0))
    return {"overlap_targets": common_targets, "overlap_pathways": common_paths, "pd_score": round(score, 3)}

# --------------------------------------------------------------------------------------
# Synthesis (compact summaries for LLM)
# --------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_canonical_pkpd() -> Dict[str, Any]:
    """Load canonical PK/PD data from JSON file. Cached for performance."""
    canonical_path = os.path.join("data", "dictionary", "canonical_pkpd.json")
    try:
        if os.path.exists(canonical_path):
            with open(canonical_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logging.getLogger(__name__).debug("Failed to load canonical PK/PD data: %s", e)
    return {"canonical_pk_interactions": {}, "canonical_ugt_transporter": {}}


def _normalize_drug_name(name: str) -> str:
    """Normalize drug name for matching (lowercase, strip, replace spaces/underscores)."""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _get_canonical_pk_interaction(drugA: str, drugB: str) -> Optional[Dict[str, Any]]:
    """Get canonical PK interaction data for a drug pair."""
    canonical_data = _load_canonical_pkpd()
    interactions = canonical_data.get("canonical_pk_interactions", {})
    
    # Try both orderings: A|B and B|A
    norm_a = _normalize_drug_name(drugA)
    norm_b = _normalize_drug_name(drugB)
    
    key1 = f"{norm_a}|{norm_b}"
    key2 = f"{norm_b}|{norm_a}"
    
    return interactions.get(key1) or interactions.get(key2)


def _get_canonical_ugt_transporter(drug_name: str) -> Optional[Dict[str, Any]]:
    """Get canonical UGT/transporter data for a single drug."""
    canonical_data = _load_canonical_pkpd()
    ugt_transporter = canonical_data.get("canonical_ugt_transporter", {})
    
    norm_name = _normalize_drug_name(drug_name)
    return ugt_transporter.get(norm_name)


def _enhance_roles_with_canonical(drug_name: str, roles: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    """Enhance enzyme/transporter roles with canonical data."""
    canonical = _get_canonical_ugt_transporter(drug_name)
    if not canonical:
        return roles
    
    # Add UGT isoforms
    ugt_data = canonical.get("ugts", [])
    for ugt_entry in ugt_data:
        isoform = ugt_entry.get("isoform", "").lower()
        role = ugt_entry.get("role", "").lower()
        if isoform and role:
            # UGTs are typically substrates, but can be inhibitors
            if role == "substrate":
                roles.setdefault("substrate", set()).add(isoform)
            elif role == "inhibitor":
                roles.setdefault("inhibitor", set()).add(isoform)
    
    # Add transporters (note: transporters are separate from enzymes in our current structure)
    # For now, we'll note them but they're handled differently in the PK analysis
    # This could be extended to add a "transporters" key to roles if needed
    
    return roles


def summarize_pkpd_risk(drugA: str, drugB: str, mech: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate compact PK/PD summaries + details to be surfaced in the prompt.
    Enhanced with UniProt, KEGG, and Reactome pathway data.
    """
    roles = extract_pk_roles(mech)
    
    # Enhance roles with canonical UGT/transporter data
    roles["a"] = _enhance_roles_with_canonical(drugA, roles.get("a", {"substrate": set(), "inhibitor": set(), "inducer": set()}))
    roles["b"] = _enhance_roles_with_canonical(drugB, roles.get("b", {"substrate": set(), "inhibitor": set(), "inducer": set()}))
    
    overlaps = detect_pk_overlaps(roles)
    pd = pd_overlap(mech)
    
    # Check for canonical PK interaction data
    canonical_interaction = _get_canonical_pk_interaction(drugA, drugB)

    # Enhanced PK analysis with KEGG
    try:
        from src.retrieval import kegg_client as kg
        kegg_metabolism_a = kg.get_metabolism_pathway(drugA)
        kegg_metabolism_b = kg.get_metabolism_pathway(drugB)
        
        # Add KEGG enzyme information to PK analysis
        if kegg_metabolism_a and kegg_metabolism_a.get("enzymes"):
            kegg_enzymes_a = [e.get("enzyme_name", "") for e in kegg_metabolism_a["enzymes"] if e.get("enzyme_name")]
            # Try to match KEGG enzymes to CYP names
            for kegg_enzyme in kegg_enzymes_a:
                kegg_lower = kegg_enzyme.lower()
                if "cytochrome p450" in kegg_lower or "cyp" in kegg_lower:
                    import re
                    cyp_match = re.search(r"cyp\s*(\d+[a-z]?\d*)", kegg_lower, re.IGNORECASE)
                    if cyp_match:
                        cyp_canon = f"cyp{cyp_match.group(1).lower()}"
                        # Add to roles if not already present (roles uses sets, not lists)
                        if "substrate" not in roles["a"]:
                            roles["a"]["substrate"] = set()
                        roles["a"]["substrate"].add(cyp_canon)
        
        if kegg_metabolism_b and kegg_metabolism_b.get("enzymes"):
            kegg_enzymes_b = [e.get("enzyme_name", "") for e in kegg_metabolism_b["enzymes"] if e.get("enzyme_name")]
            for kegg_enzyme in kegg_enzymes_b:
                kegg_lower = kegg_enzyme.lower()
                if "cytochrome p450" in kegg_lower or "cyp" in kegg_lower:
                    import re
                    cyp_match = re.search(r"cyp\s*(\d+[a-z]?\d*)", kegg_lower, re.IGNORECASE)
                    if cyp_match:
                        cyp_canon = f"cyp{cyp_match.group(1).lower()}"
                        # Add to roles if not already present (roles uses sets, not lists)
                        if "substrate" not in roles["b"]:
                            roles["b"]["substrate"] = set()
                        roles["b"]["substrate"].add(cyp_canon)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("KEGG integration failed: %s", e)

    # Recalculate overlaps with enhanced data
    overlaps = detect_pk_overlaps(roles)

    pk_flags: List[str] = []
    
    # If canonical interaction exists, use it as primary source (highest priority)
    if canonical_interaction:
        mechanism = canonical_interaction.get("mechanism", "")
        direction = canonical_interaction.get("direction", "")
        severity = canonical_interaction.get("severity", "")
        primary_affected = canonical_interaction.get("primary_affected_drug", "")
        evidence_level = canonical_interaction.get("evidence_level", "")
        
        # Build canonical PK flag
        direction_symbol = "↑" if direction == "increase" else "↓" if direction == "decrease" else ""
        severity_label = severity.replace("_", " ").title() if severity else ""
        
        canonical_flag = f"CANONICAL PK INTERACTION ({evidence_level.replace('_', ' ').title()}): {mechanism}"
        if primary_affected:
            canonical_flag += f" → {direction_symbol} {primary_affected} exposure"
        if severity_label:
            canonical_flag += f" (Severity: {severity_label})"
        
        pk_flags.insert(0, canonical_flag)  # Insert at beginning (highest priority)
    else:
        # Fallback to detected overlaps if no canonical data
        if overlaps["inhibition"]:
            pk_flags.append("Potential ↑ exposure via inhibition at " + ", ".join(sorted(overlaps["inhibition"])))
        if overlaps["induction"]:
            pk_flags.append("Potential ↓ exposure via induction at " + ", ".join(sorted(overlaps["induction"])))
        if overlaps["shared_substrate"]:
            pk_flags.append("Both are substrates of " + ", ".join(sorted(overlaps["shared_substrate"])) + " (competition possible)")

    # Enhanced PD analysis with Reactome and KEGG pathways
    enhanced_pathways = []
    try:
        from src.retrieval import reactome_client as rc
        from src.retrieval import uniprot_client as uc
        
        # Get UniProt IDs from targets
        targets_a = mech.get("targets_a", []) or []
        targets_b = mech.get("targets_b", []) or []
        
        # Extract UniProt IDs from target strings/URIs
        uniprot_ids_a = []
        uniprot_ids_b = []
        
        for target in targets_a:
            target_str = str(target)
            # Try to extract UniProt ID (format: P12345 or Q12345)
            import re
            uniprot_match = re.search(r"([PQO][0-9A-Z]{5})", target_str, re.IGNORECASE)
            if uniprot_match:
                uniprot_ids_a.append(uniprot_match.group(1).upper())
        
        for target in targets_b:
            target_str = str(target)
            uniprot_match = re.search(r"([PQO][0-9A-Z]{5})", target_str, re.IGNORECASE)
            if uniprot_match:
                uniprot_ids_b.append(uniprot_match.group(1).upper())
        
        # Get Reactome pathways for targets
        if uniprot_ids_a or uniprot_ids_b:
            all_uniprot_ids = list(set(uniprot_ids_a + uniprot_ids_b))
            reactome_pathways = rc.get_common_pathways_for_proteins(all_uniprot_ids[:10])  # Limit to avoid too many queries
            for pathway in reactome_pathways[:5]:  # Top 5
                pathway_name = pathway.get("pathway_name", "")
                if pathway_name:
                    enhanced_pathways.append(pathway_name)
        
        # Get KEGG common pathways
        try:
            from src.retrieval import kegg_client as kg
            kegg_common = kg.get_common_pathways(drugA, drugB)
            for pathway in kegg_common[:3]:  # Top 3
                pathway_name = pathway.get("pathway_name", "")
                if pathway_name:
                    enhanced_pathways.append(f"KEGG: {pathway_name}")
        except Exception:
            pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Reactome/UniProt integration failed: %s", e)

    pd_flags: List[str] = []
    if pd["overlap_targets"]:
        pd_flags.append("Overlapping targets: " + ", ".join(pd["overlap_targets"][:8]))
    if pd["overlap_pathways"]:
        pd_flags.append("Common pathways: " + ", ".join(pd["overlap_pathways"][:8]))
    if enhanced_pathways:
        pd_flags.append("Enhanced pathways: " + ", ".join(enhanced_pathways[:5]))

    # Include canonical interaction data in pk_detail if available
    pk_detail = {"roles": roles, "overlaps": overlaps}
    if canonical_interaction:
        pk_detail["canonical_interaction"] = canonical_interaction
    
    return {
        "pk_summary": "; ".join(pk_flags) if pk_flags else "No strong PK overlap detected",
        "pd_summary": "; ".join(pd_flags) if pd_flags else "No obvious PD overlap",
        "pk_detail": pk_detail,
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
    fallback_targets_a: Iterable[Any] | None = None,
    fallback_targets_b: Iterable[Any] | None = None,
) -> Dict[str, Any]:
    """
    Merge mechanistic info, using DuckDB DrugBank targets as PD fallback when QLever
    doesn't provide targets. Keeps shape required by the context schema.
    Ensures enzymes/targets/pathways are normalized and that any dict-shaped targets
    (with {'uri','label'}) are converted to normalized label strings.
    """
    q = qlever_mech or {}

    # --- Enzymes: normalize per role per side (stringify to be safe) ---
    raw_ez = q.get("enzymes", {}) or {"a": {}, "b": {}}
    def canon_roles(side_map: Dict[str, Any]) -> Dict[str, List[str]]:
        return {
            "substrate": [canonicalize_enzyme(_stringify_item(x)) for x in (side_map.get("substrate") or [])],
            "inhibitor": [canonicalize_enzyme(_stringify_item(x)) for x in (side_map.get("inhibitor") or [])],
            "inducer":   [canonicalize_enzyme(_stringify_item(x)) for x in (side_map.get("inducer")   or [])],
        }
    enzymes = {
        "a": canon_roles(raw_ez.get("a", {}) or {}),
        "b": canon_roles(raw_ez.get("b", {}) or {}),
    }

    # --- Targets / pathways / diseases: normalize whether from QLever or fallback ---
    t_a = q.get("targets_a")
    t_b = q.get("targets_b")
    p_a = q.get("pathways_a")
    p_b = q.get("pathways_b")
    cp  = q.get("common_pathways")
    d_a = q.get("diseases_a")  # NEW: diseases from DISEASE index
    d_b = q.get("diseases_b")  # NEW: diseases from DISEASE index

    targets_a = canonicalize_list(t_a if t_a else (fallback_targets_a or []))
    targets_b = canonicalize_list(t_b if t_b else (fallback_targets_b or []))
    pathways_a = canonicalize_list(p_a or [])
    pathways_b = canonicalize_list(p_b or [])
    common_pathways = canonicalize_list(cp or [])
    diseases_a = canonicalize_list(d_a or [])  # NEW
    diseases_b = canonicalize_list(d_b or [])  # NEW

    mech: Dict[str, Any] = {
        "enzymes": enzymes,
        "targets_a": targets_a,
        "targets_b": targets_b,
        "diseases_a": diseases_a,  # NEW
        "diseases_b": diseases_b,  # NEW
        "pathways_a": pathways_a,
        "pathways_b": pathways_b,
        "common_pathways": common_pathways,
    }
    return mech
