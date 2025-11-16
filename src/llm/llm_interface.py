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
TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "5000"))
TEMPLATES_PATH = os.getenv(
    "PROMPT_TEMPLATES",
    os.path.join(os.path.dirname(__file__), "prompt_templates.txt"),
)

# Decoding defaults tuned for grounded, low-variance text
DECODE_DEFAULTS = {
    "temperature": 0.2,
    "top_k": 20,
    "top_p": 0.9,
    "repeat_penalty": 1.05,
    "num_ctx": 8192,
}

DEFAULT_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "-1"))

# -------- Minimal built-in templates (used only if file is missing) --------
DEFAULT_TEMPLATES: Dict[str, str] = {
    "DOCTOR": (
        "[TEMPLATE:DOCTOR]\n"
        "SYSTEM:\n"
        "You are a clinical pharmacologist writing a concise, evidence-grounded DDI consult note for clinicians.\n"
        "Use the evidence in CONTEXT as your primary source. When you use general pharmacology knowledge\n"
        "outside this dataset, you MUST mark it clearly as such.\n\n"
        "USER:\n"
        "{{USER_QUESTION}}\n\n"
        "CONTEXT:\n"
        "- PK summary: {{PK_SUMMARY}}\n"
        "- PD summary: {{PD_SUMMARY}}\n"
        "- Real-world signal summary (FAERS, associative only): {{FAERS_SUMMARY}}\n"
        "- Risk flags (PRR, DILI, DICT, DIQT): {{RISK_FLAGS}}\n"
        "- Mechanistic evidence (enzymes/targets/pathways, possibly with PDB-like IDs): {{EVIDENCE_TABLE}}\n"
        "- Sources: {{SOURCES}}\n"
        "- Limitations / caveats: {{CAVEATS}}\n"
        "- Conversation history: {{HISTORY}}\n"
        "- Raw context JSON: {{RAW_CONTEXT_JSON}}\n\n"
        "RESPONSE STYLE:\n"
        "1) Assessment\n"
        "2) Mechanism & Rationale\n"
        "3) Expected Clinical Effects\n"
        "4) Monitoring / Actions\n"
        "5) Evidence & Uncertainty\n"
    ),
    "PATIENT": (
        "[TEMPLATE:PATIENT]\n"
        "SYSTEM:\n"
        "You are a trusted pharmacist counseling a patient. Be clear, calm, and honest.\n"
        "Use the provided CONTEXT and general pharmacology knowledge only for background explanation.\n\n"
        "USER:\n"
        "{{USER_QUESTION}}\n\n"
        "CONTEXT (simplified):\n"
        "- How they may affect each other: {{PK_SUMMARY}} / {{PD_SUMMARY}}\n"
        "- Side effects seen in reports (association only): {{FAERS_SUMMARY}}\n"
        "- Risk flags: {{RISK_FLAGS}}\n"
        "- Sources: {{SOURCES}}\n"
        "- Prior discussion: {{HISTORY}}\n"
        "- Caveats: {{CAVEATS}}\n\n"
        "RESPONSE STYLE:\n"
        "Short Answer; What to Watch For; What To Do; Extra Notes.\n"
    ),
    "PHARMA": (
        "[TEMPLATE:PHARMA]\n"
        "SYSTEM:\n"
        "You are preparing a pharmacovigilance risk brief for internal safety teams.\n"
        "Use the provided CONTEXT as your primary evidence, and clearly separate any general pharmacology knowledge.\n\n"
        "USER:\n"
        "{{USER_QUESTION}}\n\n"
        "CONTEXT:\n"
        "- PK: {{PK_SUMMARY}}\n"
        "- PD: {{PD_SUMMARY}}\n"
        "- FAERS (associative only): {{FAERS_SUMMARY}}\n"
        "- Risk flags: {{RISK_FLAGS}}\n"
        "- Mechanistic evidence snapshot: {{EVIDENCE_TABLE}}\n"
        "- Sources: {{SOURCES}}\n"
        "- Caveats: {{CAVEATS}}\n"
        "- History: {{HISTORY}}\n"
        "- Raw context JSON: {{RAW_CONTEXT_JSON}}\n\n"
        "RESPONSE STYLE:\n"
        "Overview; Evidence Summary; Risk Assessment; Recommendations; Limitations.\n"
    ),
}

def _load_templates(path: str) -> Dict[str, str]:
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
            if line.strip().startswith("SAFETY:"):
                continue
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf).strip()
    return blocks

_loaded = _load_templates(TEMPLATES_PATH)
TEMPLATES = _loaded if _loaded else {k: v.split("\n", 1)[1].strip() for k, v in DEFAULT_TEMPLATES.items()}

# ========== Public API ==========
def generate_response(
    context: Dict[str, Any],
    mode: str,
    *,
    seed: Optional[int] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    history: Optional[List[Dict[str, str]]] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    prompt = build_prompt(context or {}, mode, history=history)

    selected_model = model_name or MODEL_NAME
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
        import requests
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
        return _fallback(f"Ollama exception: {e}")


def build_prompt(
    context: Dict[str, Any],
    mode: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    history = history or []
    tpl = _select_template(mode)
    hist_block, hist_flag = _format_history(history, budget_chars=1200)

    blocks = _summarize_context(context or {}, mode)

    user_question = _extract_user_question(context, history)
    if not user_question or not user_question.strip():
        mode_lower = (mode or "").lower()
        if mode_lower in {"patient", "pt"}:
            blocks["USER_QUESTION"] = f"Can I take {blocks.get('DRUG_A', 'drug A')} and {blocks.get('DRUG_B', 'drug B')} together?"
        elif mode_lower in {"pharma", "pv", "safety", "pharmacovigilance", "pharmaceuticals"}:
            blocks["USER_QUESTION"] = f"Prepare a risk brief for {blocks.get('DRUG_A', 'drug A')} + {blocks.get('DRUG_B', 'drug B')}."
        else:
            blocks["USER_QUESTION"] = f"Evaluate potential interactions between {blocks.get('DRUG_A', 'drug A')} and {blocks.get('DRUG_B', 'drug B')} and provide a clinician-facing summary."
    else:
        blocks["USER_QUESTION"] = user_question
    blocks["RAW_CONTEXT_JSON"] = _compact_json(context, max_chars=3000)
    blocks["HISTORY"] = hist_block or "No prior conversation."

    prompt = ""
    if hist_block:
        prompt += "## HISTORY (previous turns, summarized)\n" + hist_block + "\n\n"

    if user_question and user_question.strip():
        is_followup = (
            not user_question.startswith("Evaluate potential") and
            not user_question.startswith("Can I take") and
            not user_question.startswith("Prepare a risk brief")
        )
        if is_followup:
            question_lower = user_question.lower()
            conditions = []
            if "high blood pressure" in question_lower or "hypertension" in question_lower:
                conditions.append("high blood pressure (hypertension)")
            if "elderly" in question_lower or "older" in question_lower or "age" in question_lower:
                conditions.append("elderly age")
            if "diabetes" in question_lower:
                conditions.append("diabetes")
            if "kidney" in question_lower or "renal" in question_lower:
                conditions.append("renal impairment")
            if "liver" in question_lower or "hepatic" in question_lower:
                conditions.append("hepatic impairment")

            prompt += "## CRITICAL: ANSWER THIS SPECIFIC QUESTION DIRECTLY\n"
            prompt += f"USER QUESTION: {user_question}\n\n"
            prompt += "MANDATORY INSTRUCTIONS:\n"
            prompt += "1. DO NOT repeat the generic interaction assessment; this is a follow-up about a specific scenario.\n"
            prompt += "2. You MUST directly address this scenario and relate it to the interaction evidence.\n"
            if conditions:
                prompt += f"3. The question specifically mentions: {', '.join(conditions)}. You MUST address each of these.\n"
            prompt += "\n4. Structure your answer so it:\n"
            prompt += "   - Starts with a direct answer for that scenario\n"
            prompt += "   - Lists specific problems/risks for that scenario\n"
            prompt += "   - Explains how the condition interacts with the DDI\n"
            prompt += "   - Provides condition-specific monitoring/management (qualitative only)\n"
            prompt += "\n5. Use ALL available evidence from CONTEXT (risk flags, FAERS, PK/PD, mechanistic evidence).\n\n"

    prompt += _fill(tpl, **blocks)

    policy = (
        "\n\n[POLICY]\n"
        "- Use ONLY the evidence listed under CONTEXT and Sources as your primary evidence about THIS dataset.\n"
        "- FAERS and PRR signals are associative, not causal; state this clearly in Evidence & Uncertainty.\n"
        "- You MAY use general pharmacology knowledge in two ways:\n"
        "  1) Neutral annotations (e.g., human-readable protein/gene names and broad roles for structural IDs).\n"
        "  2) Widely accepted mechanisms (e.g., that a drug is a substrate/inhibitor of a well-known CYP), used qualitatively.\n"
        "  In both cases, explicitly mark this as 'general pharmacology knowledge, not directly observed in the retrieved datasets'.\n"
        "- Do NOT propose specific numeric dose changes, titration schedules, or laboratory thresholds (INR, QTc, ULN multiples,\n"
        "  exact monitoring intervals) unless those exact numbers appear in CONTEXT. Use qualitative phrases instead:\n"
        "  'periodic monitoring', 'closer monitoring around initiation or dose changes', 'dose adjustment may be needed'.\n"
        "- Do NOT invent specific study results or guideline statements that are not supported by CONTEXT.\n"
        "- Do NOT name specific alternative medicines unless they are explicitly provided in CONTEXT or Sources. You MAY describe\n"
        "  drug CLASSES (e.g., 'a PPI with lower interaction potential') without naming individual agents.\n"
        "- If PK summary says 'No strong PK overlap detected', then the retrieved mechanistic data do not show a PK interaction.\n"
        "  You may still state well-known PK mechanisms from general pharmacology knowledge, but you MUST phrase them as such and\n"
        "  avoid asserting that the dataset itself demonstrates them.\n"
        "- For structural target IDs (e.g., PDB-like IDs), you MAY provide human-readable labels and broad biological roles when\n"
        "  known, and you MAY hypothesize how shared binding could contribute to PD overlap, but you MUST:\n"
        "  (a) Mark these as hypotheses based on general pharmacology knowledge, and\n"
        "  (b) Avoid presenting them as proven clinical mechanisms unless explicit pathway/PD data in CONTEXT support them.\n"
        "- Avoid logical contradictions: do not say 'no PK overlap' in the dataset and then claim a firm PK mechanism is proven by\n"
        "  these data. If external knowledge suggests a mechanism, clearly separate 'not demonstrated in retrieved data' from\n"
        "  'suggested by general pharmacology knowledge'.\n"
    )
    prompt += policy

    if blocks.get("__TRUNCATED__") or hist_flag:
        prompt += "\n\n[Note: some context was truncated for length.]"

    return prompt

# ========== Template helpers ==========
def _select_template(mode: str) -> str:
    key_raw = (mode or "").strip().lower()
    if key_raw in {"doc", "doctor", "physician", "clinician"}:
        key = "DOCTOR"
    elif key_raw in {"patient", "pt"}:
        key = "PATIENT"
    elif key_raw in {"pharma", "pv", "safety", "pharmacovigilance", "pharmaceuticals"}:
        key = "PHARMA"
    else:
        key = (mode or "").strip().upper() or "DOCTOR"

    if key in TEMPLATES and TEMPLATES[key].strip():
        return TEMPLATES[key]
    if "DOCTOR" in TEMPLATES and TEMPLATES["DOCTOR"].strip():
        return TEMPLATES["DOCTOR"]
    return (
        "SYSTEM:\nYou are a helpful assistant.\n\n"
        "USER:\nProvide a grounded summary.\n\n"
        "CONTEXT:\n{{PK_SUMMARY}}\n{{PD_SUMMARY}}\n{{FAERS_SUMMARY}}\n"
    )

def _fill(template: str, **kwargs: str) -> str:
    out = template
    for k, v in kwargs.items():
        if k.startswith("__"):
            continue
        # Handle empty values - replace with empty string or "(none)" for certain fields
        if v == "" and k in ["PK_META"]:
            # For PK_META, if empty, remove the entire line
            out = re.sub(rf"- Additional PK metadata.*?{{{{PK_META}}}}\s*\n", "", out)
        out = out.replace(f"{{{{{k}}}}}", v if v else "")
    out = re.sub(r"\{\{[A-Z0-9_]+\}\}", "(no data)", out)
    # Clean up any empty PK_META lines that might remain
    out = re.sub(r"- Additional PK metadata.*?\(none\)\s*\n", "", out)
    return out

# ========== History formatting ==========
def _format_history(
    history: List[Dict[str, str]], budget_chars: int = 1200
) -> Tuple[str, bool]:
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

    truncated = False
    out: List[str] = []
    for line in reversed(rows):
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
    history = history or []
    for msg in reversed(history):
        role = (msg.get("role") or "").lower()
        if role.startswith("u"):
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

    pk_txt, pk_len, pk_prio = _format_pk(ctx)
    pk_meta_txt = _format_pk_meta(ctx)  # NEW: PK metadata from PubChem
    pd_txt, pd_len, pd_prio = _format_pd(ctx)
    faers_txt, faers_len = _format_faers(ctx, limit=5)
    flags_txt = _format_flags(ctx)
    table_txt = _format_evidence_table(ctx, mode)
    sources_txt = _format_sources(ctx)
    caveats_txt = _format_caveats(ctx)

    if (mode or "").lower() in {"doctor", "physician", "pharma", "pv", "safety"}:
        budget = 2400
    else:
        budget = 1500

    parts: List[Tuple[str, int, int, str]] = [
        ("PK", pk_len, pk_prio, pk_txt),
        ("PK_META", len(pk_meta_txt), 2, pk_meta_txt),  # NEW: PK metadata (high priority, after PK)
        ("PD", pd_len, pd_prio, pd_txt),
        ("FAERS", faers_len, 5, faers_txt),
        ("Flags", len(flags_txt), 10, flags_txt),
        ("Table", len(table_txt), 20, table_txt),
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
        "PK_META": g("PK_META", ""),  # NEW: PK metadata from PubChem (empty if not available)
        "PD_SUMMARY": g("PD", "(no PD evidence)"),
        "FAERS_SUMMARY": g("FAERS", "No evidence from FAERS."),
        "RISK_FLAGS": g("Flags", "(no tabular risk flags)"),
        "EVIDENCE_TABLE": g("Table", "(no evidence table)"),
        "SOURCES": g("Sources", "(no sources listed)"),
        "CAVEATS": g("Caveats", "(none)"),
        "__TRUNCATED__": "1" if (truncated_by_budget or input_overflow) else "",
    }

# ========== Formatting helpers ==========
def _format_pk_meta(ctx: Dict[str, Any]) -> str:
    """
    Format PK metadata from PubChem (qualitative properties like logP, molecular weight, etc.)
    Returns a compact string for inclusion in prompt.
    """
    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    pk_data_a = mech.get("pk_data_a", {}) or {}
    pk_data_b = mech.get("pk_data_b", {}) or {}
    
    def format_pk_props(pk_data: Dict[str, Any], label: str) -> List[str]:
        """Format PK properties for one drug."""
        props = []
        if pk_data.get("log_p") is not None:
            logp = pk_data["log_p"]
            if logp > 3:
                props.append(f"high logP ({logp:.1f}) → lipophilic")
            elif logp < 0:
                props.append(f"low logP ({logp:.1f}) → hydrophilic")
        if pk_data.get("molecular_weight") is not None:
            mw = pk_data["molecular_weight"]
            props.append(f"MW {mw:.0f}")
        if pk_data.get("h_bond_donors") is not None and pk_data.get("h_bond_acceptors") is not None:
            hbd = pk_data["h_bond_donors"]
            hba = pk_data["h_bond_acceptors"]
            if hbd > 3 or hba > 5:
                props.append(f"H-bonds: {hbd}D/{hba}A")
        return props
    
    parts_a = format_pk_props(pk_data_a, "A")
    parts_b = format_pk_props(pk_data_b, "B")
    
    if not parts_a and not parts_b:
        return ""
    
    result = []
    if parts_a:
        result.append(f"Drug A: {', '.join(parts_a)}")
    if parts_b:
        result.append(f"Drug B: {', '.join(parts_b)}")
    
    return "; ".join(result) if result else ""


def _format_pk(ctx: Dict[str, Any]) -> Tuple[str, int, int]:
    pkpd = (ctx.get("pkpd") or {})
    if pkpd.get("pk_summary"):
        txt = str(pkpd["pk_summary"])
        # Add DATA-DRIVEN prefix if not already present
        if "DATA-DRIVEN PK OVERLAP:" not in txt:
            # Check if it's an overlap or no overlap message
            if any(x in txt.lower() for x in ("inhibition", "induction", "overlap", "substrate")):
                # Try to extract the key line and prefix it
                if "No strong PK overlap" in txt or "no strong pk overlap" in txt.lower():
                    txt = txt.replace("No strong PK overlap", "DATA-DRIVEN PK OVERLAP: No strong PK overlap")
                    txt = txt.replace("no strong pk overlap", "DATA-DRIVEN PK OVERLAP: no strong pk overlap")
                elif "PK overlap" in txt or "pk overlap" in txt.lower():
                    # Find the overlap line and prefix it
                    lines = txt.split('.')
                    for i, line in enumerate(lines):
                        if "overlap" in line.lower() and "DATA-DRIVEN" not in line:
                            lines[i] = "DATA-DRIVEN PK OVERLAP: " + line.strip()
                            break
                    txt = '. '.join(lines)
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
        key_line = f"DATA-DRIVEN PK OVERLAP: Key PK overlap in retrieved mechanistic data: {overlap_txt}"
        prio = 0
    else:
        key_line = "DATA-DRIVEN PK OVERLAP: No strong PK overlap detected in the retrieved mechanistic data."
        prio = 3

    txt = f"{a_str}. {b_str}. {key_line}"
    return txt, len(txt), prio

def _format_pd(ctx: Dict[str, Any]) -> Tuple[str, int, int]:
    pkpd = (ctx.get("pkpd") or {})
    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    
    # Always include pathways from mechanistic data (KEGG/Reactome)
    pathways_a = _as_str_list(mech.get("pathways_a", []))
    pathways_b = _as_str_list(mech.get("pathways_b", []))
    common_pathways_mech = _as_str_list(mech.get("common_pathways", []))
    
    # If pkpd summary exists, use it but enhance with mechanistic pathways if missing
    if pkpd.get("pd_summary"):
        txt = str(pkpd["pd_summary"])
        # If summary doesn't mention pathways but we have them, append them
        if ("pathway" not in txt.lower() or "enhanced pathway" not in txt.lower()) and (pathways_a or pathways_b or common_pathways_mech):
            pathway_parts = []
            if common_pathways_mech:
                pathway_parts.append("Common pathways (KEGG/Reactome): " + ", ".join(sorted(set(common_pathways_mech))[:5]))
            if pathways_a:
                pathway_parts.append(f"Drug A pathways: {', '.join(sorted(set(pathways_a))[:3])}")
            if pathways_b:
                pathway_parts.append(f"Drug B pathways: {', '.join(sorted(set(pathways_b))[:3])}")
            if pathway_parts:
                txt += "; " + "; ".join(pathway_parts)
        prio = 1 if ("overlapping" in txt.lower() or "pathways" in txt.lower()) else 4
        return txt, len(txt), prio

    # Fallback: build from mechanistic data
    cp = common_pathways_mech
    ta = _as_str_list(mech.get("targets_a", []))
    tb = _as_str_list(mech.get("targets_b", []))
    overlap_targets = sorted(set(ta) & set(tb))

    parts = []
    if cp:
        parts.append("Common pathways: " + ", ".join(sorted(set(cp))[:8]))
    elif pathways_a or pathways_b:
        # Include individual pathways if no common ones
        if pathways_a:
            parts.append(f"Drug A pathways: {', '.join(sorted(set(pathways_a))[:5])}")
        if pathways_b:
            parts.append(f"Drug B pathways: {', '.join(sorted(set(pathways_b))[:5])}")
    else:
        parts.append("No common pathways found")
    if overlap_targets:
        parts.append("Overlapping targets: " + ", ".join(overlap_targets[:8]))
    else:
        parts.append("No overlapping targets found")

    txt = "; ".join(parts)
    prio = 1 if (cp or overlap_targets or pathways_a or pathways_b) else 4
    return txt, len(txt), prio

def _format_faers(ctx: Dict[str, Any], limit: int = 5) -> Tuple[str, int]:
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
    pa = _as_str_list(mech.get("pathways_a", []))[:8]  # Increased from 6 to 8
    pb = _as_str_list(mech.get("pathways_b", []))[:8]  # Increased from 6 to 8
    cp_mech = _as_str_list(mech.get("common_pathways", []))[:8]  # Include common pathways

    lines = [
        f"A IDs: {ids('a')} | B IDs: {ids('b')}",
        f"A targets: {', '.join(ta) if ta else '(none)'}",
        f"B targets: {', '.join(tb) if tb else '(none)'}",
    ]
    # Always include pathways if available (KEGG/Reactome)
    if cp_mech:
        lines.append(f"Common pathways (KEGG/Reactome): {', '.join(cp_mech)}")
    if pa:
        lines.append(f"A pathways: {', '.join(pa)}")
    if pb:
        lines.append(f"B pathways: {', '.join(pb)}")
    if not cp_mech and not pa and not pb:
        lines.append("Pathways: (none)")
    
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
    text = f"Unable to generate a full explanation right now. {msg}"
    return {
        "text": _append_disclaimer(text, "patient"),
        "usage": {},
        "meta": {"error": True},
    }
