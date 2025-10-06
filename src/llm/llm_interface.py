# src/llm/llm_interface.py
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

# ========== Configuration ==========
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "mistral-nemo:12b")  # e.g., 'mistral:instruct'
TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "60"))
TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "prompt_templates.txt")


# ========== Templates loader ==========
def _load_templates(path: str) -> Dict[str, str]:
    """
    Load [TEMPLATE:NAME] blocks from a plaintext file into a dict: {NAME: body}.
    Missing file returns {} so callers can still proceed gracefully.
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    blocks: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []
    for line in raw.splitlines():
        m = re.match(r"^\[TEMPLATE:([A-Z_]+)\]\s*$", line.strip())
        if m:
            if current is not None:
                blocks[current] = "\n".join(buf).strip()
            current = m.group(1).strip().upper()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf).strip()
    return blocks


TEMPLATES = _load_templates(TEMPLATES_PATH)


# ========== Public API ==========
def generate_response(
    context: Dict[str, Any],
    mode: str,
    *,
    seed: Optional[int] = None,
    temperature: float = 0.2,
    max_tokens: int = 800,
) -> Dict[str, Any]:
    """
    Call a local Ollama HTTP endpoint and return:
      {"text": str, "usage": {...}, "meta": {...}}

    - Appends a concise mode-specific medical disclaimer.
    - Handles network/timeout errors via a safe fallback.
    - Avoids hallucinated sources by only using passed-in context.
    """
    prompt = build_prompt(context or {}, mode)

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "options": {
            "temperature": float(temperature),
            # Some Ollama builds accept num_predict; keep it explicit.
            "num_predict": int(max_tokens),
            "num_ctx": 8192,
            **({"seed": int(seed)} if seed is not None else {}),
        },
        "stream": False,
    }

    try:
        import requests  # lazy import so tests can monkeypatch without dependency at import time

        r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=TIMEOUT_S)
        if r.status_code != 200:
            return _fallback(f"Ollama error {r.status_code}: {r.text[:200]}")

        data = r.json()
        text = (data.get("response") or "").strip()
        return {
            "text": _append_disclaimer(text, mode),
            "usage": {
                "eval_count": data.get("eval_count"),
                "prompt_eval_count": data.get("prompt_eval_count"),
            },
            "meta": {
                "model": MODEL_NAME,
                "temperature": temperature,
                "seed": seed,
            },
        }
    except Exception as e:
        # Includes connection error, timeout, JSON decode issues, etc.
        return _fallback(f"Ollama exception: {e}")


def build_prompt(context: Dict[str, Any], mode: str) -> str:
    """
    Build a mode-conditioned prompt from normalized context.
    Enforces:
      - Instruction-first templates.
      - Explicit “No evidence from X” when blocks are empty.
      - Truncation policy prioritizing PK/PD overlaps → FAERS → rest.
    """
    tpl = _select_template(mode)
    blocks = _summarize_context(context or {}, mode)
    prompt = _fill(tpl, **blocks)
    if blocks.get("__TRUNCATED__"):
        prompt += "\n\n[Note: some context was truncated for length.]"
    return prompt


# ========== Template helpers ==========
def _select_template(mode: str) -> str:
    key = (mode or "").strip().upper()
    if key not in TEMPLATES:
        key = "PATIENT"  # safe default tone
    return TEMPLATES.get(key, "")


def _fill(template: str, **kwargs: str) -> str:
    """Simple {{PLACEHOLDER}} replacement; leaves unknown placeholders as '(no data)'."""
    out = template
    for k, v in kwargs.items():
        if k.startswith("__"):
            continue
        out = out.replace(f"{{{{{k}}}}}", v)
    # Sanitize any still-unreplaced placeholders
    out = re.sub(r"\{\{[A-Z0-9_]+\}\}", "(no data)", out)
    return out


# ========== Context summarization & truncation ==========
def _summarize_context(ctx: Dict[str, Any], mode: str) -> Dict[str, str]:
    drugs = ctx.get("drugs", {}) or {}
    drug_a = (drugs.get("a", {}) or {}).get("name", "Drug A")
    drug_b = (drugs.get("b", {}) or {}).get("name", "Drug B")

    # Compose sections
    pk_txt, pk_len, pk_prio = _format_pk(ctx)
    pd_txt, pd_len, pd_prio = _format_pd(ctx)
    faers_txt, faers_len = _format_faers(ctx, limit=5)
    flags_txt = _format_flags(ctx)
    table_txt = _format_evidence_table(ctx, mode)
    sources_txt = _format_sources(ctx)
    caveats_txt = _format_caveats(ctx)

    # Truncation policy (approx char budgets, not tokens)
    # Always keep: drug names/IDs (these live inside the table block), strongest PK/PD overlaps,
    # then FAERS top-5 per drug, then the rest.
    if (mode or "").lower() in {"doctor", "pharma"}:
        budget = 2400
    else:
        budget = 1500  # patient = simpler, shorter

    # Priority: lower number = kept earlier
    parts: List[Tuple[str, int, int, str]] = [
        ("PK", pk_len, pk_prio, pk_txt),
        ("PD", pd_len, pd_prio, pd_txt),
        ("FAERS", faers_len, 5, faers_txt),
        ("Flags", len(flags_txt), 10, flags_txt),
        ("Table", len(table_txt), 20, table_txt),  # includes IDs; still lower priority than core PK/PD/FAERS
        ("Sources", len(sources_txt), 50, sources_txt),
        ("Caveats", len(caveats_txt), 60, caveats_txt),
    ]

    used = 0
    truncated = False
    kept: Dict[str, str] = {}

    # Sort by priority
    for label, ln, prio, txt in sorted(parts, key=lambda x: x[2]):
        if used + ln <= budget:
            kept[label] = txt
            used += ln
        else:
            # Try to include a concise slice if it's a long block
            if ln > 220:
                kept[label] = (txt[:200].rstrip() + " …")
                used += 203
                truncated = True
            else:
                truncated = True
                # drop entirely

    # Extraction helpers
    def g(label: str, default: str = "(truncated or missing)") -> str:
        return kept.get(label, default)

    return {
        "DRUG_A": drug_a,
        "DRUG_B": drug_b,
        "PK_SUMMARY": g("PK", "(no PK evidence)"),
        "PD_SUMMARY": g("PD", "(no PD evidence)"),
        "FAERS_SUMMARY": g("FAERS", "No evidence from FAERS (none)"),
        "RISK_FLAGS": g("Flags", "(no tabular risk flags)"),
        "EVIDENCE_TABLE": g("Table", "(no evidence table)"),
        "SOURCES": g("Sources", "(no sources listed)"),
        "CAVEATS": g("Caveats", "(none)"),
        "__TRUNCATED__": "1" if truncated else "",
    }


# ========== Formatting helpers ==========
def _format_pk(ctx: Dict[str, Any]) -> Tuple[str, int, int]:
    """
    Return (text, length, priority).
    Priority is lower (i.e., more important) if there is a plausible enzyme overlap:
      A substrate X & B inhibitor X, or vice versa.
    """
    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    enz = mech.get("enzymes", {}) or {}
    a = enz.get("a", {}) or {}
    b = enz.get("b", {}) or {}

    def part(lbl: str, items: List[str]) -> str:
        return f"{lbl}=" + (", ".join(sorted(set(items))) if items else "none")

    a_str = "; ".join(
        [
            part("A substrate", a.get("substrate", []) or []),
            part("A inhibitor", a.get("inhibitor", []) or []),
            part("A inducer", a.get("inducer", []) or []),
        ]
    ) or "No enzyme data for Drug A"

    b_str = "; ".join(
        [
            part("B substrate", b.get("substrate", []) or []),
            part("B inhibitor", b.get("inhibitor", []) or []),
            part("B inducer", b.get("inducer", []) or []),
        ]
    ) or "No enzyme data for Drug B"

    # Detect key overlaps (substrate vs inhibitor/inducer)
    a_sub = set(a.get("substrate", []) or [])
    b_sub = set(b.get("substrate", []) or [])
    a_inh = set(a.get("inhibitor", []) or [])
    b_inh = set(b.get("inhibitor", []) or [])
    a_ind = set(a.get("inducer", []) or [])
    b_ind = set(b.get("inducer", []) or [])

    inhib_overlap = (a_sub & b_inh) | (b_sub & a_inh)
    indu_overlap = (a_sub & b_ind) | (b_sub & a_ind)

    overlaps = []
    if inhib_overlap:
        overlaps.append("inhibition at " + ", ".join(sorted(inhib_overlap)))
    if indu_overlap:
        overlaps.append("induction at " + ", ".join(sorted(indu_overlap)))

    overlap_txt = ", ".join(overlaps) if overlaps else "none detected"
    txt = f"{a_str}. {b_str}. Key PK overlap: {overlap_txt}"
    prio = 0 if overlaps else 3  # stronger priority when overlaps exist
    return txt, len(txt), prio


def _format_pd(ctx: Dict[str, Any]) -> Tuple[str, int, int]:
    """
    Return (text, length, priority).
    Elevate priority if there are common pathways or overlapping targets.
    """
    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    cp = mech.get("common_pathways", []) or []
    ta = mech.get("targets_a", []) or []
    tb = mech.get("targets_b", []) or []
    overlap_targets = sorted(set(ta) & set(tb))

    parts = []
    if cp:
        parts.append("Common pathways: " + ", ".join(sorted(set(cp))[:8]))
    else:
        parts.append("No common pathways found")
    if overlap_targets:
        parts.append("Overlapping targets: " + ", ".join(overlap_targets[:8]))
    else:
        parts.append("No overlapping targets found")

    txt = "; ".join(parts)
    prio = 1 if (cp or overlap_targets) else 4
    return txt, len(txt), prio


def _format_faers(ctx: Dict[str, Any], limit: int = 5) -> Tuple[str, int]:
    """
    Produce compact FAERS lines for A, B, and combo. If no data, say so explicitly.
    """
    faers = (ctx.get("signals", {}) or {}).get("faers", {}) or {}

    def topfmt(key: str, label: str) -> str:
        items = faers.get(key, []) or []
        if not items:
            return f"{label}: No evidence from FAERS"
        top = ", ".join([f"{term} (n={cnt})" for term, cnt in items[:limit]])
        return f"{label}: {top}"

    a = topfmt("top_reactions_a", "Drug A")
    b = topfmt("top_reactions_b", "Drug B")
    combo = topfmt("combo_reactions", "Combo")

    txt = " | ".join([a, b, combo])
    return txt, len(txt)


def _format_flags(ctx: Dict[str, Any]) -> str:
    tab = (ctx.get("signals", {}) or {}).get("tabular", {}) or {}

    def val(key: str, default: str = "unknown") -> str:
        v = tab.get(key)
        return str(v) if (v is not None) else default

    flags = [
        f"PRR(pair)={val('prr','NA')}",
        f"DILI(A)={val('dili_a')}",
        f"DILI(B)={val('dili_b')}",
        f"DICT(A)={val('dict_a')}",
        f"DICT(B)={val('dict_b')}",
        f"DIQT(A)={val('diqt_a','NA')}",
        f"DIQT(B)={val('diqt_b','NA')}",
    ]
    return ", ".join(flags)


def _format_evidence_table(ctx: Dict[str, Any], mode: str) -> str:
    """
    Very compact line list to avoid prompt bloat.
    Includes IDs (PubChem/DrugBank) so clinicians and UI can anchor entities.
    """
    drugs = ctx.get("drugs", {}) or {}

    def ids(side: str) -> str:
        d = (drugs.get(side, {}) or {}).get("ids", {}) or {}
        parts: List[str] = []
        if d.get("pubchem_cid"):
            parts.append(f"CID={d['pubchem_cid']}")
        if d.get("drugbank"):
            parts.append(f"DrugBank={d['drugbank']}")
        return ("; ".join(parts)) or "(no IDs)"

    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    lines = [
        f"A IDs: {ids('a')} | B IDs: {ids('b')}",
        f"A targets: {', '.join(mech.get('targets_a', [])[:8]) or '(none)'}",
        f"B targets: {', '.join(mech.get('targets_b', [])[:8]) or '(none)'}",
        f"A pathways: {', '.join(mech.get('pathways_a', [])[:6]) or '(none)'}",
        f"B pathways: {', '.join(mech.get('pathways_b', [])[:6]) or '(none)'}",
    ]
    return " | ".join(lines)


def _format_sources(ctx: Dict[str, Any]) -> str:
    src = ctx.get("sources", {}) or {}

    def fmt(key: str) -> str:
        lst = src.get(key, []) or []
        return f"{key}: " + (", ".join(lst) if lst else "(none)")

    return "; ".join([fmt("duckdb"), fmt("qlever"), fmt("openfda")])


def _format_caveats(ctx: Dict[str, Any]) -> str:
    cv = ctx.get("caveats", []) or []
    return "; ".join(cv) if cv else "(none)"


# ========== Disclaimers & fallback ==========
def _append_disclaimer(text: str, mode: str) -> str:
    m = (mode or "").lower()
    if m == "patient":
        d = "\n\nNote: This is informational and not medical advice. Always consult your doctor or pharmacist."
    elif m == "doctor":
        d = "\n\nDisclaimer: Research prototype. Use alongside clinical judgment and approved labeling."
    else:
        d = "\n\nDisclaimer: Informational, non-regulatory summary; defer to internal PV/labeling."
    return (text or "").rstrip() + d


def _fallback(msg: str) -> Dict[str, Any]:
    """
    Safe user-facing fallback, patient-tone disclaimer.
    """
    text = f"Unable to generate a full explanation right now. {msg}"
    return {
        "text": _append_disclaimer(text, "patient"),
        "usage": {},
        "meta": {"error": True},
    }
