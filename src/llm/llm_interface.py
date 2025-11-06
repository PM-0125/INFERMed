# src/llm/llm_interface.py
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    # Load .env from project root (go up from src/llm to project root)
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    load_dotenv(env_path, override=False)  # Don't override existing env vars
except ImportError:
    pass  # python-dotenv not installed, skip

# ========== Configuration ==========
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "gpt-oss")
TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "1000"))
TEMPLATES_PATH = os.getenv(
    "PROMPT_TEMPLATES",
    os.path.join(os.path.dirname(__file__), "prompt_templates.txt"),
)

# Decoding defaults tuned for grounded, low-variance text
DECODE_DEFAULTS = {
    "temperature": 0.2,
    "top_k": 20,            # harmless if the model ignores it
    "top_p": 0.9,           # harmless if the model ignores it
    "repeat_penalty": 1.05, # harmless if the model ignores it
    "num_ctx": 8192,
}

# Allow unlimited tokens by default unless user overrides
DEFAULT_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "-1"))  # -1 == unlimited for Ollama

# -------- Minimal built-in templates (used only if file is missing) --------
DEFAULT_TEMPLATES: Dict[str, str] = {
    "DOCTOR": (
        "[TEMPLATE:DOCTOR]\n"
        "SYSTEM:\nYou are a clinical pharmacologist. Be specific, grounded, and actionable.\n\n"
        "USER:\nEvaluate potential interactions between {{DRUG_A}} and {{DRUG_B}}.\n\n"
        "CONTEXT:\n"
        "- PK: {{PK_SUMMARY}}\n- PD: {{PD_SUMMARY}}\n- FAERS: {{FAERS_SUMMARY}}\n"
        "- Risk flags: {{RISK_FLAGS}}\n- Evidence: {{EVIDENCE_TABLE}}\n"
        "- Sources: {{SOURCES}}\n- Caveats: {{CAVEATS}}\n- History: {{HISTORY}}\n\n"
        "RESPONSE STYLE:\n"
        "1) Assessment\n2) Mechanism & rationale\n3) Expected clinical effects\n"
        "4) Monitoring / actions\n5) Evidence & uncertainty\n"
    ),
    "PATIENT": (
        "[TEMPLATE:PATIENT]\n"
        "SYSTEM:\nYou are a trusted pharmacist counseling a patient. Be clear and calm.\n\n"
        "USER:\nCan I take {{DRUG_A}} and {{DRUG_B}} together?\n\n"
        "CONTEXT:\n"
        "- What to know: {{PK_SUMMARY}} / {{PD_SUMMARY}}\n- Reports (associative): {{FAERS_SUMMARY}}\n"
        "- Risk flags: {{RISK_FLAGS}}\n- Sources: {{SOURCES}}\n- Caveats: {{CAVEATS}}\n- History: {{HISTORY}}\n\n"
        "RESPONSE STYLE:\n"
        "Short answer; What to watch for; What to do; Extra notes.\n"
    ),
    "PHARMA": (
        "[TEMPLATE:PHARMA]\n"
        "SYSTEM:\nYou are preparing a pharmacovigilance risk brief for safety teams.\n\n"
        "USER:\nPrepare a risk brief for {{DRUG_A}} + {{DRUG_B}}.\n\n"
        "CONTEXT:\n"
        "- PK: {{PK_SUMMARY}}\n- PD: {{PD_SUMMARY}}\n- FAERS: {{FAERS_SUMMARY}}\n"
        "- Risk flags: {{RISK_FLAGS}}\n- Evidence: {{EVIDENCE_TABLE}}\n- Sources: {{SOURCES}}\n"
        "- Caveats: {{CAVEATS}}\n- History: {{HISTORY}}\n\n"
        "RESPONSE STYLE:\n"
        "Overview; Evidence summary; Risk assessment; Recommendations; Limitations.\n"
    ),
}

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
            # Drop any legacy "SAFETY:" lines (we append disclaimers in code)
            if line.strip().startswith("SAFETY:"):
                continue
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf).strip()
    return blocks

# Try to load from file first; fall back to built-ins if empty/missing
_loaded = _load_templates(TEMPLATES_PATH)
TEMPLATES = _loaded if _loaded else {k: v.split("\n", 1)[1].strip() for k, v in DEFAULT_TEMPLATES.items()}

# ========== Public API ==========
def generate_response(
    context: Dict[str, Any],
    mode: str,
    *,
    seed: Optional[int] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,  # None => unlimited (or env override)
    history: Optional[List[Dict[str, str]]] = None,  # conversational history support
    model_name: Optional[str] = None,  # Override default model
) -> Dict[str, Any]:
    """
    Call a local Ollama HTTP endpoint and return:
      {"text": str, "usage": {...}, "meta": {...}}

    - Appends a concise mode-specific medical disclaimer.
    - Handles network/timeout errors via a safe fallback.
    - Avoids hallucinated sources by only using passed-in context.
    - Supports lightweight conversational history (list of {"role":"user"|"assistant", "text": str}).
    - model_name: Optional model name override (defaults to OLLAMA_MODEL env var or "gpt-oss").
    """
    prompt = build_prompt(context or {}, mode, history=history)

    # Use provided model_name, or fall back to environment/default
    selected_model = model_name or MODEL_NAME

    # Decide num_predict: explicit arg > env > unlimited (-1)
    num_predict = int(max_tokens) if (max_tokens is not None) else DEFAULT_NUM_PREDICT

    payload_options = {
        **DECODE_DEFAULTS,
        "temperature": float(temperature),
        "num_predict": num_predict,
        **({"seed": int(seed)} if seed is not None else {}),
    }
    payload = {
        "model": selected_model,
        "prompt": prompt,
        "options": payload_options,
        "stream": False,
    }

    try:
        import requests  # lazy import so tests can monkeypatch at import time
        r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=TIMEOUT_S)
        if r.status_code != 200:
            return _fallback(f"Ollama error {r.status_code}: {r.text[:200]}")

        data = r.json()
        text = (data.get("response") or "").strip()
        text = _strip_template_safety(text)
        return {
            "text": _append_disclaimer(text, mode),
            "usage": {
                "eval_count": data.get("eval_count"),
                "prompt_eval_count": data.get("prompt_eval_count"),
            },
            "meta": {
                "model": selected_model,
                "temperature": temperature,
                "seed": seed,
                "ts": int(time.time()),
            },
        }
    except Exception as e:
        # Includes connection error, timeout, JSON decode issues, etc.
        return _fallback(f"Ollama exception: {e}")


def build_prompt(
    context: Dict[str, Any],
    mode: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Build a mode-conditioned prompt from normalized context.
    Enforces:
      - Instruction-first templates.
      - Explicit FAERS wording when blocks are empty.
      - Truncation policy prioritizing PK/PD overlaps → FAERS → rest.
      - Lightweight history framing so follow-ups stay grounded.

      Template placeholders supported:
        {{DRUG_A}}, {{DRUG_B}}, {{PK_SUMMARY}}, {{PD_SUMMARY}},
        {{FAERS_SUMMARY}}, {{RISK_FLAGS}}, {{EVIDENCE_TABLE}},
        {{SOURCES}}, {{CAVEATS}}, {{USER_QUESTION}}, {{RAW_CONTEXT_JSON}},
        {{HISTORY}}
    """
    history = history or []
    tpl = _select_template(mode)

    # Compact history (for MODE note + optional template usage)
    hist_block, hist_flag = _format_history(history, budget_chars=1200)

    # Summarize context blocks
    blocks = _summarize_context(context or {}, mode)

    # Inject optional extras used by templates
    blocks["USER_QUESTION"] = _extract_user_question(context, history)
    blocks["RAW_CONTEXT_JSON"] = _compact_json(context, max_chars=3000)
    blocks["HISTORY"] = hist_block or "No prior conversation."

    prompt = ""
    if hist_block:
        prompt += "## HISTORY (previous turns, summarized)\n" + hist_block + "\n\n"

    prompt += _fill(tpl, **blocks)

    # Grounding policy (auto-appended)
    policy = (
        "\n\n[POLICY]\n"
        "- Use ONLY the evidence listed under CONTEXT and Sources. Base your answer primarily on this evidence.\n"
        "- If FAERS has no items for a given section, say so explicitly (see instructions below).\n"
        "- You may add general, widely accepted clinical considerations "
        "(e.g. elderly are more sensitive to CNS depressants, renal impairment increases exposure of renally cleared drugs), "
        "but clearly label these as general considerations.\n"
        "- Do NOT propose specific numeric dose changes, percentage adjustments, titration schedules, "
        "or explicit INR targets unless those exact numbers appear in CONTEXT. "
        "Use qualitative language instead, such as 'dose reduction may be needed' or 'closer INR monitoring is advised'.\n"
        "- Do NOT invent specific study results or guideline statements that are not supported by CONTEXT.\n"
        "- Do NOT name specific alternative medicines unless they are explicitly mentioned in CONTEXT or Sources; "
        "you may suggest drug classes instead (e.g. 'an antifungal with less CYP inhibition').\n"
        "- If you mention a potential PK mechanism that is not clearly supported by the mechanistic evidence in CONTEXT "
        "(e.g. a known CYP interaction from general pharmacology knowledge), "
        "explicitly mark it as 'general pharmacology knowledge, not directly observed in the retrieved datasets'.\n"
        "- Avoid contradictions: do not say 'no PK overlap' and then immediately describe a PK overlap. "
        "If evidence is weak or conflicting, say 'PK overlap uncertain; possible mechanism is…'.\n"
    )
    prompt += policy

    if blocks.get("__TRUNCATED__") or hist_flag:
        prompt += "\n\n[Note: some context was truncated for length.]"

    return prompt


# ========== Template helpers ==========
def _select_template(mode: str) -> str:
    """
    Resolve the template by mode (aliases allowed).
    Fallback policy:
      - Unknown mode -> DOCTOR tone
      - Missing template body -> DOCTOR tone (built-in if file absent)
    """
    key_raw = (mode or "").strip().lower()
    if key_raw in {"doc", "doctor", "physician", "clinician"}:
        key = "DOCTOR"
    elif key_raw in {"patient", "pt"}:
        key = "PATIENT"
    elif key_raw in {"pharma", "pv", "safety", "pharmacovigilance", "pharmaceuticals"}:
        key = "PHARMA"
    else:
        key = (mode or "").strip().upper() or "DOCTOR"

    # Prefer requested key; if not present, fall back to DOCTOR; if still missing, return any available
    if key in TEMPLATES and TEMPLATES[key].strip():
        return TEMPLATES[key]
    if "DOCTOR" in TEMPLATES and TEMPLATES["DOCTOR"].strip():
        return TEMPLATES["DOCTOR"]
    # Absolute last resort: a tiny neutral template
    return "SYSTEM:\nYou are a helpful assistant.\n\nUSER:\nProvide a grounded summary.\n\nCONTEXT:\n{{PK_SUMMARY}}\n{{PD_SUMMARY}}\n{{FAERS_SUMMARY}}\n"


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


# ========== History formatting ==========
def _format_history(
    history: List[Dict[str, str]], budget_chars: int = 1200
) -> Tuple[str, bool]:
    """
    Turn a list of {"role": "user"|"assistant", "text": str} into a compact block.
    Clips from the front if over budget (keeps the most recent turns).
    Returns (block_text, truncated_flag).
    """
    if not history:
        return "", False

    rows: List[str] = []
    total = 0
    for h in history:
        role = (h.get("role") or "").lower()
        role = "User" if role.startswith("u") else ("Assistant" if role.startswith("a") else "User")
        text = (h.get("text") or "").strip()
        if not text:
            continue
        rows.append(f"- {role}: {text}")

    # Keep most recent lines within budget
    truncated = False
    out: List[str] = []
    for line in reversed(rows):  # newest first
        if total + len(line) + 1 <= budget_chars:
            out.append(line)
            total += len(line) + 1
        else:
            truncated = True
            break
    out.reverse()
    return "\n".join(out), truncated


# ========== Small helpers ==========
def _as_str_list(xs: Any) -> List[str]:
    """Coerce lists that may contain dicts ({'label','uri'}) or strings into clean strings."""
    out: List[str] = []
    for x in xs or []:
        if isinstance(x, dict):
            s = (x.get("label") or x.get("uri") or "").strip()
        else:
            s = str(x).strip()
        if s:
            out.append(s)
    return out


def _extract_user_question(
    ctx: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None
) -> str:
    """
    Prefer the last user message from history as the current question.
    Fall back to context['meta']['question'] or ctx['query'].
    """
    history = history or []
    for msg in reversed(history):
        role = (msg.get("role") or "").lower()
        if role.startswith("u"):  # user
            t = (msg.get("text") or "").strip()
            if t:
                return t

    q = ((ctx.get("meta") or {}).get("question")) or ctx.get("query") or ""
    return str(q).strip()


def _compact_json(obj: Any, max_chars: int = 3000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        s = "{}"
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "…"
    return s


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
    if (mode or "").lower() in {"doctor", "physician", "pharma", "pv", "safety"}:
        budget = 2400
    else:
        budget = 1500  # patient = simpler, shorter

    # Priority: lower number = kept earlier
    parts: List[Tuple[str, int, int, str]] = [
        ("PK", pk_len, pk_prio, pk_txt),
        ("PD", pd_len, pd_prio, pd_txt),
        ("FAERS", faers_len, 5, faers_txt),
        ("Flags", len(flags_txt), 10, flags_txt),
        ("Table", len(table_txt), 20, table_txt),  # includes IDs
        ("Sources", len(sources_txt), 50, sources_txt),
        ("Caveats", len(caveats_txt), 60, caveats_txt),
    ]

    used = 0
    truncated_by_budget = False
    kept: Dict[str, str] = {}

    for label, ln, prio, txt in sorted(parts, key=lambda x: x[2]):
        if used + ln <= budget:
            kept[label] = txt
            used += ln
        else:
            if ln > 220:
                kept[label] = (txt[:200].rstrip() + " …")
                used += 203
            truncated_by_budget = True

    # Mark truncation if the *input lists* exceeded display caps (even if final fits)
    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    faers = (ctx.get("signals", {}) or {}).get("faers", {}) or {}
    input_overflow = False
    if len(mech.get("targets_a", []) or []) > 8:
        input_overflow = True
    if len(mech.get("targets_b", []) or []) > 8:
        input_overflow = True
    if len(mech.get("pathways_a", []) or []) > 6:
        input_overflow = True
    if len(mech.get("pathways_b", []) or []) > 6:
        input_overflow = True
    if len(mech.get("common_pathways", []) or []) > 8:
        input_overflow = True
    if len(faers.get("top_reactions_a", []) or []) > 5:
        input_overflow = True
    if len(faers.get("top_reactions_b", []) or []) > 5:
        input_overflow = True
    if len(faers.get("combo_reactions", []) or []) > 5:
        input_overflow = True

    def g(label: str, default: str = "(truncated or missing)") -> str:
        return kept.get(label, default)

    return {
        "DRUG_A": drug_a,
        "DRUG_B": drug_b,
        "PK_SUMMARY": g("PK", "(no PK evidence)"),
        "PD_SUMMARY": g("PD", "(no PD evidence)"),
        "FAERS_SUMMARY": g("FAERS", "No evidence from FAERS."),
        "RISK_FLAGS": g("Flags", "(no tabular risk flags)"),
        "EVIDENCE_TABLE": g("Table", "(no evidence table)"),
        "SOURCES": g("Sources", "(no sources listed)"),
        "CAVEATS": g("Caveats", "(none)"),
        "__TRUNCATED__": "1" if (truncated_by_budget or input_overflow) else "",
    }


# ========== Formatting helpers ==========
def _format_pk(ctx: Dict[str, Any]) -> Tuple[str, int, int]:
    """
    Return (text, length, priority).

    - Priority is lower (i.e., more important) if there is a plausible enzyme overlap:
      A substrate X & B inhibitor X, or vice versa (and same for inducers).
    - Prefer precomputed context['pkpd']['pk_summary'] when present.
    """
    pkpd = (ctx.get("pkpd") or {})
    if pkpd.get("pk_summary"):
        txt = str(pkpd["pk_summary"])
        prio = 0 if any(x in txt.lower() for x in ("inhibition", "induction")) else 3
        return txt, len(txt), prio

    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    enz = mech.get("enzymes", {}) or {}
    a = enz.get("a", {}) or {}
    b = enz.get("b", {}) or {}

    def part(lbl: str, items: List[str]) -> str:
        clean = sorted(set(_as_str_list(items)))
        return f"{lbl}=" + (", ".join(clean) if clean else "none")

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
    a_sub = set(_as_str_list(a.get("substrate", [])))
    b_sub = set(_as_str_list(b.get("substrate", [])))
    a_inh = set(_as_str_list(a.get("inhibitor", [])))
    b_inh = set(_as_str_list(b.get("inhibitor", [])))
    a_ind = set(_as_str_list(a.get("inducer", [])))
    b_ind = set(_as_str_list(b.get("inducer", [])))

    inhib_overlap = (a_sub & b_inh) | (b_sub & a_inh)
    indu_overlap = (a_sub & b_ind) | (b_sub & a_ind)

    overlaps: List[str] = []
    if inhib_overlap:
        overlaps.append("inhibition at " + ", ".join(sorted(inhib_overlap)))
    if indu_overlap:
        overlaps.append("induction at " + ", ".join(sorted(indu_overlap)))

    if overlaps:
        overlap_txt = ", ".join(sorted(overlaps))
        key_line = f"Key PK overlap in retrieved mechanistic data: {overlap_txt}"
        prio = 0
    else:
        key_line = "No strong PK overlap detected in the retrieved mechanistic data."
        prio = 3

    txt = f"{a_str}. {b_str}. {key_line}"
    return txt, len(txt), prio


def _format_pd(ctx: Dict[str, Any]) -> Tuple[str, int, int]:
    """
    Return (text, length, priority).
    Elevate priority if there are common pathways or overlapping targets.
    Prefer precomputed context['pkpd']['pd_summary'] when present.
    """
    pkpd = (ctx.get("pkpd") or {})
    if pkpd.get("pd_summary"):
        txt = str(pkpd["pd_summary"])
        prio = 1 if ("overlapping" in txt.lower() or "pathways" in txt.lower()) else 4
        return txt, len(txt), prio

    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    cp = _as_str_list(mech.get("common_pathways", []))
    ta = _as_str_list(mech.get("targets_a", []))
    tb = _as_str_list(mech.get("targets_b", []))
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
    Produce compact FAERS lines for A, B, and combo.
    For single drugs, 'No FAERS data in the queried subset' is used when empty.
    For the combination, distinguish 'no co-reports' from general 'no FAERS signals'.
    """
    faers = (ctx.get("signals", {}) or {}).get("faers", {}) or {}

    a_items = faers.get("top_reactions_a", []) or []
    b_items = faers.get("top_reactions_b", []) or []
    combo_items = faers.get("combo_reactions", []) or []

    def fmt_single(label: str, items: List[Tuple[str, int]]) -> str:
        if not items:
            return f"{label}: No FAERS data in the queried subset."
        top = ", ".join([f"{term} (n={cnt})" for term, cnt in items[:limit]])
        return f"{label}: {top}"

    a = fmt_single("Drug A", a_items)
    b = fmt_single("Drug B", b_items)

    if combo_items:
        combo = "Combo: " + ", ".join(
            [f"{term} (n={cnt})" for term, cnt in combo_items[:limit]]
        )
    else:
        if a_items or b_items:
            combo = (
                "Combo: No specific FAERS co-reports for this pair were found "
                "in the subset queried; adverse events are derived from single-drug reports."
            )
        else:
            combo = "Combo: No FAERS data in the queried subset for either drug."

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

    ta = _as_str_list(mech.get("targets_a", []))[:8]
    tb = _as_str_list(mech.get("targets_b", []))[:8]
    pa = _as_str_list(mech.get("pathways_a", []))[:6]
    pb = _as_str_list(mech.get("pathways_b", []))[:6]

    lines = [
        f"A IDs: {ids('a')} | B IDs: {ids('b')}",
        f"A targets: {', '.join(ta) if ta else '(none)'}",
        f"B targets: {', '.join(tb) if tb else '(none)'}",
        f"A pathways: {', '.join(pa) if pa else '(none)'}",
        f"B pathways: {', '.join(pb) if pb else '(none)'}",
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
def _strip_template_safety(text: str) -> str:
    """Remove any safety lines that may have been printed by legacy templates."""
    if not text:
        return text
    patterns = [
        r'^\s*"This is research software; final decisions rest with licensed clinicians."\s*$',
        r'^\s*This is research software; final decisions rest with licensed clinicians\.\s*$',
        r'^\s*Disclaimer:\s*Research prototype\..*$',
    ]
    lines = text.splitlines()
    kept = []
    for ln in lines:
        if any(re.match(p, ln.strip()) for p in patterns):
            continue
        kept.append(ln)
    return "\n".join(kept).rstrip()


def _append_disclaimer(text: str, mode: str) -> str:
    m = (mode or "").lower()
    if m in {"patient", "pt"}:
        d = "\n\nNote: This is informational and not medical advice. Always consult your doctor or pharmacist."
    elif m in {"doctor", "physician", "clinician"}:
        d = "\n\nDisclaimer: Research prototype. Use alongside clinical judgment and approved labeling."
    else:
        d = "\n\nDisclaimer: Informational, non-regulatory summary; defer to internal PV/labeling."
    return (text or "").rstrip() + d


def _fallback(msg: str) -> Dict[str, Any]:
    """Safe user-facing fallback, patient-tone disclaimer."""
    text = f"Unable to generate a full explanation right now. {msg}"
    return {
        "text": _append_disclaimer(text, "patient"),
        "usage": {},
        "meta": {"error": True},
    }
