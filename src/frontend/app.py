import os
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import pandas as pd
import plotly.express as px

# Project imports
from src.llm.rag_pipeline import (
    get_context_cached,
    retrieve_and_normalize,
    run_rag,
    clear_context_cache,     # to clear disk cache for a pair
    record_feedback,         # for user feedback tracking
)
from src.llm.llm_interface import generate_response  # used for follow-ups when context is already present


# =========================
# Logging setup (file + console)
# =========================
LOG_DIR = os.getenv("INFERMED_LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "app.log")

logger = logging.getLogger("infermed.app")
logger.setLevel(logging.DEBUG)

# Avoid duplicate handlers when Streamlit reruns
if not logger.handlers:
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(console)

# Capture warnings into logging
logging.captureWarnings(True)


def log_event(event: str, **fields):
    try:
        msg = json.dumps({"event": event, **fields}, ensure_ascii=False)
    except Exception:
        msg = f"{event} | (unserializable fields)"
    logger.info(msg)


# ------------------------------
# Small helpers
# ------------------------------
APP_TITLE = "INFERMed"
DEFAULT_QUESTION = "Please explain the interaction, monitoring, and actions."
MODE_DOCTOR = "Doctor"
MODE_PATIENT = "Patient"
MODE_PV_RESEARCH = "Pharmacovigilance / Research"
AUDIENCE_MODES = [MODE_DOCTOR, MODE_PATIENT, MODE_PV_RESEARCH]
FEEDBACK_MODES = {MODE_DOCTOR, MODE_PV_RESEARCH}
SAMPLE_PAIRS = [
    ("warfarin", "fluconazole"),
    ("simvastatin", "clarithromycin"),
    ("clopidogrel", "omeprazole"),
    ("digoxin", "verapamil"),
    ("sildenafil", "nitroglycerin"),
]

# Medical color scheme (DrugBank/PubChem inspired)
MEDICAL_COLORS = {
    "primary": "#1E88E5",  # Medical blue
    "secondary": "#43A047",  # Medical green
    "warning": "#FB8C00",  # Warning orange
    "danger": "#E53935",  # Alert red
    "light_bg": "#F5F7FA",
    "card_bg": "#FFFFFF",
    "text_primary": "#1A1A1A",
    "text_secondary": "#6B7280",
    "border": "#E5E7EB",
}


def _inject_custom_css():
    """Inject product styling inspired by PubChem and DrugBank."""
    css = f"""
    <style>
    :root {{
        --im-navy: #10324A;
        --im-blue: #1B70A6;
        --im-cyan: #2CA7C9;
        --im-green: #2F8F5B;
        --im-mint: #E7F6F1;
        --im-ink: #17212B;
        --im-muted: #607284;
        --im-line: #D7E2EA;
        --im-soft: #F4F8FB;
        --im-card: #FFFFFF;
    }}
    [data-testid="stAppViewContainer"] {{
        background:
            linear-gradient(180deg, #E9F4F8 0, #F6F8FB 220px, #F6F8FB 100%);
        color: var(--im-ink);
    }}
    .main .block-container {{ max-width: 1340px; padding-top: 1rem; padding-bottom: 2rem; }}
    [data-testid="stHeader"] {{
        visibility: visible;
        background: rgba(255, 255, 255, 0.92);
        border-bottom: 1px solid rgba(215, 226, 234, 0.95);
        backdrop-filter: blur(8px);
    }}
    header {{ visibility: visible; }}
    [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ visibility: hidden; }}
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="stSidebarCollapseButton"],
    button[aria-label*="sidebar" i] {{
        visibility: visible !important;
        opacity: 1 !important;
        display: inline-flex !important;
        z-index: 10000 !important;
    }}
    h1, h2, h3 {{ color: var(--im-navy); letter-spacing: 0; }}
    h1 {{ font-weight: 760; margin: 0; }}
    h2, h3 {{ font-weight: 720; }}
    .product-shell {{
        background: #FFFFFF;
        border: 1px solid var(--im-line);
        border-radius: 8px;
        margin-bottom: 1rem;
        box-shadow: 0 14px 32px rgba(16, 50, 74, 0.08);
        overflow: hidden;
    }}
    .product-topbar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding: 0.85rem 1.1rem;
        background: var(--im-navy);
        color: #FFFFFF;
    }}
    .brand-cluster {{ display: flex; align-items: center; gap: 0.75rem; }}
    .brand-mark {{
        width: 34px;
        height: 34px;
        border-radius: 7px;
        background: #31A872;
        color: #FFFFFF;
        display: grid;
        place-items: center;
        font-weight: 800;
        font-size: 1rem;
        border: 1px solid rgba(255,255,255,0.35);
    }}
    .brand-name {{ font-size: 1.18rem; font-weight: 760; letter-spacing: 0; }}
    .brand-subtitle {{ font-size: 0.77rem; color: #C6E6F4; margin-top: 0.05rem; }}
    .top-nav {{ display: flex; gap: 0.4rem; flex-wrap: wrap; justify-content: flex-end; }}
    .top-nav a {{
        color: #DFF4FB;
        text-decoration: none;
        font-size: 0.82rem;
        padding: 0.35rem 0.55rem;
        border-radius: 6px;
        border: 1px solid rgba(255,255,255,0.16);
    }}
    .top-nav a:hover {{ background: rgba(255,255,255,0.1); }}
    .search-band {{
        padding: 1.15rem;
        background: #FFFFFF;
        border-top: 4px solid var(--im-cyan);
    }}
    .search-title {{
        font-size: 1.45rem;
        font-weight: 760;
        color: var(--im-navy);
        margin: 0 0 0.25rem 0;
    }}
    .search-subtitle {{ color: var(--im-muted); font-size: 0.95rem; margin-bottom: 0.6rem; }}
    .section-label {{
        color: #386072;
        font-size: 0.76rem;
        font-weight: 760;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        margin: 0.6rem 0 0.35rem;
    }}
    .result-grid {{ margin-top: 1rem; }}
    .drug-card {{
        background: #FFFFFF;
        border: 1px solid var(--im-line);
        border-top: 5px solid var(--im-blue);
        border-radius: 8px;
        padding: 1.15rem;
        margin: 0.6rem 0 0.9rem;
        box-shadow: 0 10px 24px rgba(16, 50, 74, 0.06);
    }}
    .evidence-card {{ background-color: #FFFFFF; border: 1px solid var(--im-line); border-radius: 8px; padding: 1rem; margin: 0.5rem 0; }}
    .drug-badge {{ display: inline-block; background-color: #EAF4FB; color: #125E88; padding: 0.2rem 0.55rem; border-radius: 6px; font-size: 0.82rem; font-weight: 650; margin: 0.2rem; border: 1px solid #B8D7EA; }}
    .source-badge {{ display: inline-block; background-color: #F7FBFD; color: #283747; padding: 0.22rem 0.6rem; border-radius: 6px; font-size: 0.75rem; border: 1px solid var(--im-line); margin: 0.2rem; }}
    .risk-indicator {{ display: inline-flex; align-items: center; justify-content: center; min-width: 7rem; padding: 0.5rem 0.85rem; border-radius: 6px; font-weight: 760; margin: 0.35rem 0; }}
    .risk-low {{ background-color: #E6F4EA; color: #226D3D; border: 1px solid #BDE1C8; }}
    .risk-moderate {{ background-color: #FFF5E4; color: #9A5600; border: 1px solid #FFD8A0; }}
    .risk-high {{ background-color: #FFE8E8; color: #B4232A; border: 1px solid #FFC6C9; }}
    .drug-link {{ color: var(--im-blue); text-decoration: none; font-weight: 650; }}
    .drug-link:hover {{ color: #0E4D76; text-decoration: underline; }}
    .stTextInput > div > div > input {{
        border-radius: 6px;
        border: 1px solid #B8CAD8;
        background: #FFFFFF;
        min-height: 2.65rem;
        font-size: 1rem;
    }}
    .stTextInput > div > div > input:focus {{ border-color: var(--im-blue); box-shadow: 0 0 0 1px var(--im-blue); }}
    .stButton > button {{
        border-radius: 6px;
        font-weight: 700;
        border: 1px solid #B8CAD8;
        min-height: 2.55rem;
    }}
    .stButton > button[kind="primary"] {{
        background: var(--im-blue);
        border-color: var(--im-blue);
        color: #FFFFFF;
    }}
    [data-testid="stSidebar"] {{ background: #EEF6F8; border-right: 1px solid var(--im-line); }}
    [data-testid="stSidebar"] h3 {{ color: var(--im-navy); }}
    [data-testid="stMetric"] {{ background: #FFFFFF; border: 1px solid var(--im-line); border-radius: 8px; padding: 0.65rem; }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 6px; border-bottom: 1px solid var(--im-line); }}
    .stTabs [data-baseweb="tab"] {{ border-radius: 6px 6px 0 0; padding: 0.65rem 1rem; color: var(--im-navy); }}
    .stChatMessage {{ border-radius: 8px; border: 1px solid var(--im-line); margin-bottom: 0.75rem; background: #FFFFFF; }}
    .stChatMessage[data-testid="user"] {{ background-color: #F1F7FB; border-left: 4px solid var(--im-blue); }}
    .stChatMessage[data-testid="assistant"] {{ background-color: #FFFFFF; border-left: 4px solid var(--im-green); }}
    .chat-panel {{
        background: #FFFFFF;
        border: 1px solid var(--im-line);
        border-top: 5px solid var(--im-green);
        border-radius: 8px;
        padding: 1rem;
        margin: 0.6rem 0 0.9rem;
        box-shadow: 0 10px 24px rgba(16, 50, 74, 0.06);
    }}
    .empty-state {{
        background: #FFFFFF;
        border: 1px solid var(--im-line);
        border-radius: 8px;
        padding: 1rem;
        margin-top: 0.8rem;
    }}
    .empty-grid {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0.65rem;
        margin-top: 0.65rem;
    }}
    .empty-tile {{
        border: 1px solid var(--im-line);
        border-radius: 8px;
        padding: 0.8rem;
        background: #F8FBFD;
        min-height: 5.8rem;
    }}
    .empty-tile strong {{ color: var(--im-navy); display: block; margin-bottom: 0.2rem; }}
    .empty-tile span {{ color: var(--im-muted); font-size: 0.84rem; }}
    .app-footer {{ text-align: center; color: var(--im-muted); padding: 1.4rem; font-size: 0.88rem; }}
    @media (max-width: 900px) {{
        .product-topbar {{ align-items: flex-start; flex-direction: column; }}
        .top-nav {{ justify-content: flex-start; }}
        .empty-grid {{ grid-template-columns: 1fr; }}
    }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
    
    # Add JavaScript to fix form accessibility issues - handles empty attributes too
    accessibility_js = """
    <script>
    (function() {
        // Fix form fields with missing or empty id/name attributes and autocomplete
        function fixFormAccessibility() {
            // Find ALL input, select, and textarea elements (including those with empty attributes)
            const formFields = document.querySelectorAll('input, select, textarea');
            
            formFields.forEach((field, index) => {
                // Fix empty or missing name attribute
                const currentName = field.getAttribute('name');
                if (!currentName || currentName.trim() === '') {
                    // Use existing id if available, otherwise generate one
                    if (!field.id || field.id.trim() === '') {
                        const fieldType = field.tagName.toLowerCase();
                        const ariaLabel = field.getAttribute('aria-label') || '';
                        const placeholder = field.getAttribute('placeholder') || '';
                        const identifier = (ariaLabel || placeholder || 'field').replace(/[^a-zA-Z0-9]/g, '_').toLowerCase();
                        field.id = `streamlit_${fieldType}_${identifier}_${index}_${Date.now()}`;
                    }
                    field.name = field.id;
                }
                
                // Fix empty autocomplete attributes (empty string is invalid)
                const currentAutocomplete = field.getAttribute('autocomplete');
                if (currentAutocomplete !== null && currentAutocomplete.trim() === '') {
                    // Infer autocomplete value from context
                    const inputType = (field.type || '').toLowerCase();
                    const placeholder = (field.placeholder || '').toLowerCase();
                    const ariaLabel = (field.getAttribute('aria-label') || '').toLowerCase();
                    const label = field.closest('.stTextInput, .stNumberInput, .stSelectbox')?.textContent?.toLowerCase() || '';
                    const context = `${placeholder} ${ariaLabel} ${label}`.toLowerCase();
                    
                    let autocompleteValue = 'off'; // Default
                    
                    if (inputType === 'email' || context.includes('email')) {
                        autocompleteValue = 'email';
                    } else if (inputType === 'tel' || context.includes('phone') || context.includes('tel')) {
                        autocompleteValue = 'tel';
                    } else if (context.includes('name') && !context.includes('model') && !context.includes('drug')) {
                        autocompleteValue = 'name';
                    } else if (context.includes('search')) {
                        autocompleteValue = 'off';
                    } else if (inputType === 'number' || field.type === 'number') {
                        autocompleteValue = 'off';
                    } else {
                        // For drug names, model names, etc. - use 'off' to prevent autocomplete
                        autocompleteValue = 'off';
                    }
                    
                    field.setAttribute('autocomplete', autocompleteValue);
                }
            });
            
            // Fix labels not associated with form fields
            const labels = document.querySelectorAll('label');
            labels.forEach((label, index) => {
                const labelFor = label.getAttribute('for');
                if (!labelFor || labelFor.trim() === '') {
                    // Try to find associated input
                    const input = label.querySelector('input, select, textarea');
                    if (input) {
                        if (!input.id || input.id.trim() === '') {
                            input.id = `streamlit_label_input_${index}_${Date.now()}`;
                        }
                        label.setAttribute('for', input.id);
                    }
                }
            });
        }
        
        // Run immediately and on page load
        fixFormAccessibility();
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', fixFormAccessibility);
        }
        
        // Run after Streamlit reruns (more aggressive monitoring)
        const observer = new MutationObserver(function() {
            setTimeout(fixFormAccessibility, 50);
        });
        
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['name', 'autocomplete', 'id']
        });
        
        // Also listen for Streamlit's render events
        window.addEventListener('load', fixFormAccessibility);
    })();
    </script>
    """
    st.markdown(accessibility_js, unsafe_allow_html=True)


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    a = (a or "").strip()
    b = (b or "").strip()
    return a, b


def _normalize_mode(mode: str) -> str:
    raw = (mode or "").strip().lower()
    if raw in {"pharma", "pv", "safety", "pharmacovigilance", "research", "pharmacovigilance / research"}:
        return MODE_PV_RESEARCH
    if raw in {"patient", "pt"}:
        return MODE_PATIENT
    return MODE_DOCTOR


def _set_search_pair(drug_a: str, drug_b: str):
    st.session_state.drugA_input = drug_a
    st.session_state.drugB_input = drug_b
    st.session_state.drugA = drug_a
    st.session_state.drugB = drug_b
    st.session_state.chat = []
    st.session_state.active_pair = ("", "")


def _fmt_metric(x, fmt="{:.2f}", default="N/A"):
    try:
        if isinstance(x, (int, float)):
            return fmt.format(x)
        return default if x in (None, "", "unknown") else str(x)
    except Exception:
        return default


def _pairs_to_df(pairs, n=10):
    rows = []
    for it in (pairs or []):
        try:
            term, cnt = it
            rows.append((str(term), int(cnt)))
        except Exception:
            continue
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["Reaction", "Count"]).head(n)


def _init_state():
    st.session_state.setdefault("mode", MODE_DOCTOR)
    st.session_state.mode = _normalize_mode(st.session_state.mode)
    st.session_state.setdefault("use_ctx_cache", True)
    st.session_state.setdefault("force_refresh", False)

    st.session_state.setdefault("drugA", "warfarin")
    st.session_state.setdefault("drugB", "fluconazole")
    st.session_state.setdefault("drugA_input", st.session_state.drugA)
    st.session_state.setdefault("drugB_input", st.session_state.drugB)

    st.session_state.setdefault("context_by_pair", {})
    st.session_state.setdefault("chat", [])
    st.session_state.setdefault("active_pair", ("", ""))
    st.session_state.setdefault("feedback_submitted", {})  # Track feedback per pair


def _render_header():
    st.set_page_config(
        page_title=f"{APP_TITLE} - Drug Interaction Analysis",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            'Get Help': 'https://github.com/your-repo/issues',
            'Report a bug': 'https://github.com/your-repo/issues',
            'About': "INFERMed: PK/PD-aware RAG DDI Explainer"
        }
    )
    _inject_custom_css()

    st.markdown(
        """
        <div class="product-shell">
          <div class="product-topbar">
            <div class="brand-cluster">
              <div class="brand-mark">IM</div>
              <div>
                <div class="brand-name">INFERMed</div>
                <div class="brand-subtitle">Drug interaction intelligence</div>
              </div>
            </div>
            <div class="top-nav">
              <a href="#interaction-search">Search</a>
              <a href="#results">Evidence</a>
              <a href="#ask">Ask AI</a>
              <a href="#sources">Sources</a>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    

def _sidebar():
    with st.sidebar:
        st.markdown('<div class="section-label">Session</div>', unsafe_allow_html=True)

        st.markdown("### Audience")
        st.session_state.mode = _normalize_mode(st.session_state.mode)
        st.session_state.mode = st.radio(
            "Select your role",
            AUDIENCE_MODES,
            index=AUDIENCE_MODES.index(st.session_state.mode),
            label_visibility="collapsed"
        )

        st.markdown("---")
        st.markdown("### Evidence")
        st.session_state.use_ctx_cache = st.checkbox("Use evidence cache", value=bool(st.session_state.use_ctx_cache))
        st.session_state.force_refresh = st.checkbox("Refresh evidence", value=bool(st.session_state.force_refresh))

        st.markdown("---")
        st.markdown('<div class="section-label">Runtime</div>', unsafe_allow_html=True)
        st.caption("Provider, model, and decoding settings are controlled by local runtime configuration.")


def _render_pair_inputs():
    st.markdown(
        """
        <div id="interaction-search" class="product-shell">
          <div class="search-band">
            <div class="search-title">Search Drug-Drug Interactions</div>
            <div class="search-subtitle">Enter two medicines to retrieve public evidence, risk signals, mechanisms, and a generated assessment.</div>
        """,
        unsafe_allow_html=True,
    )

    role_col, cache_col = st.columns([2, 1])
    with role_col:
        st.session_state.mode = _normalize_mode(st.session_state.mode)
        st.session_state.mode = st.radio(
            "Audience",
            AUDIENCE_MODES,
            index=AUDIENCE_MODES.index(st.session_state.mode),
            horizontal=True,
        )
    with cache_col:
        st.session_state.use_ctx_cache = st.checkbox("Use evidence cache", value=bool(st.session_state.use_ctx_cache))
        st.session_state.force_refresh = st.checkbox("Refresh evidence", value=bool(st.session_state.force_refresh))

    colA, colB = st.columns([1, 1])
    with colA:
        st.text_input("Drug A", key="drugA_input", placeholder="warfarin")
    with colB:
        st.text_input("Drug B", key="drugB_input", placeholder="fluconazole")

    st.session_state.drugA = (st.session_state.drugA_input or "").strip()
    st.session_state.drugB = (st.session_state.drugB_input or "").strip()

    st.markdown('<div class="section-label">Common interaction searches</div>', unsafe_allow_html=True)
    sample_cols = st.columns(len(SAMPLE_PAIRS))
    for idx, (drug_a, drug_b) in enumerate(SAMPLE_PAIRS):
        with sample_cols[idx]:
            st.button(
                f"{drug_a} + {drug_b}",
                width="stretch",
                key=f"sample_pair_{idx}",
                on_click=_set_search_pair,
                args=(drug_a, drug_b),
            )

    action_col, clear_col, refresh_col = st.columns([2, 1, 1])
    with action_col:
        if st.button("Analyze Interaction", type="primary", width="stretch", key="btn_explain_pair"):
            _handle_explain_pair()
    with clear_col:
        if st.button("Clear Conversation", width="stretch", key="btn_clear_chat"):
            st.session_state.chat.clear()
            st.toast("Conversation cleared.")
    with refresh_col:
        if st.button("Refresh Evidence", width="stretch", key="btn_refresh"):
            _refresh_current_pair()

    st.markdown("</div></div>", unsafe_allow_html=True)


def _refresh_current_pair():
    pair = _pair_key(st.session_state.drugA, st.session_state.drugB)
    if pair in st.session_state.context_by_pair:
        del st.session_state.context_by_pair[pair]
    try:
        cleared = clear_context_cache(*pair)
        if cleared:
            st.toast("Evidence cache for this pair cleared.")
    except Exception:
        logger.exception("Error clearing context cache")
    st.toast("Evidence cache cleared.")
    log_event("refresh_evidence.clicked", pair=pair)


def _maybe_load_context(a: str, b: str) -> Dict[str, Any]:
    use_cache = st.session_state.use_ctx_cache and (not st.session_state.force_refresh)
    pair = _pair_key(a, b)
    st.session_state.active_pair = pair

    log_event("maybe_load_context.begin", pair=pair, use_cache=use_cache)

    if use_cache and pair in st.session_state.context_by_pair:
        ctx = st.session_state.context_by_pair[pair]
        log_event("maybe_load_context.hit_memory", pair=pair)
        return ctx

    try:
        with st.spinner("Retrieving evidence from databases..."):
            if use_cache:
                ctx, _ = get_context_cached(a, b, force_refresh=False)
                log_event("maybe_load_context.get_context_cached", pair=pair, cached=True)
            else:
                if st.session_state.force_refresh:
                    ctx, _ = get_context_cached(a, b, force_refresh=True)
                    log_event("maybe_load_context.get_context_cached", pair=pair, cached=False, force_refresh=True)
                else:
                    ctx = retrieve_and_normalize(a, b)
                    log_event("maybe_load_context.retrieve_and_normalize", pair=pair)
            st.session_state.context_by_pair[pair] = ctx
            return ctx
    except Exception as e:
        logger.exception("Context retrieval failed")
        st.error(f"Context retrieval failed: {e}")
        return {}


def _get_drug_links(drug_info: Dict[str, Any], drug_name: str) -> str:
    ids = drug_info.get("ids", {}) or {}
    links = []

    pubchem_cid = ids.get("pubchem_cid")
    if pubchem_cid:
        links.append(
            f'<a href="https://pubchem.ncbi.nlm.nih.gov/compound/{pubchem_cid}" '
            f'target="_blank" class="drug-link">PubChem: {pubchem_cid}</a>'
        )

    drugbank_id = ids.get("drugbank")
    if drugbank_id:
        links.append(
            f'<a href="https://go.drugbank.com/drugs/{drugbank_id}" '
            f'target="_blank" class="drug-link">DrugBank: {drugbank_id}</a>'
        )

    if not links:
        return f'<span style="color: {MEDICAL_COLORS["text_secondary"]};">No IDs available</span>'

    return " · ".join(links)


def _calculate_risk_level(context: Dict[str, Any]) -> Tuple[str, str]:
    pkpd = context.get("pkpd", {}) or {}
    faers = context.get("signals", {}).get("faers", {}) or {}
    tabular = context.get("signals", {}).get("tabular", {}) or {}

    risk_score = 0
    pk_detail = pkpd.get("pk_detail", {}) or {}
    overlaps = pk_detail.get("overlaps", {}) or {}
    if overlaps.get("inhibition") or overlaps.get("induction"):
        risk_score += 2
    if overlaps.get("shared_substrate"):
        risk_score += 1

    pd_detail = pkpd.get("pd_detail", {}) or {}
    if pd_detail.get("overlap_targets") or pd_detail.get("overlap_pathways"):
        risk_score += 1

    if faers.get("combo_reactions"):
        risk_score += 1

    prr = tabular.get("prr")
    if isinstance(prr, (int, float)):
        if prr > 2.0:
            risk_score += 2
        elif prr > 1.5:
            risk_score += 1

    if risk_score >= 4:
        return "high", "High Risk"
    elif risk_score >= 2:
        return "moderate", "Moderate Risk"
    else:
        return "low", "Low Risk"


def _render_topline(context: Dict[str, Any]):
    drugs = context.get("drugs", {})
    a_info = drugs.get("a", {}) or {}
    b_info = drugs.get("b", {}) or {}
    a_name = a_info.get("name") or st.session_state.drugA
    b_name = b_info.get("name") or st.session_state.drugB

    st.markdown(f'<div class="drug-card">', unsafe_allow_html=True)

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f'### {a_name} + {b_name}')
    with col2:
        risk_level, risk_label = _calculate_risk_level(context)
        st.markdown(
            f'<div class="risk-indicator risk-{risk_level}">{risk_label}</div>',
            unsafe_allow_html=True
        )

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**{a_name}**")
        st.markdown(_get_drug_links(a_info, a_name), unsafe_allow_html=True)
    with col_b:
        st.markdown(f"**{b_name}**")
        st.markdown(_get_drug_links(b_info, b_name), unsafe_allow_html=True)

    pkpd = context.get("pkpd", {}) or {}
    pk_summary = pkpd.get("pk_summary", "(none)")
    pd_summary = pkpd.get("pd_summary", "(none)")

    if pk_summary != "(none)" or pd_summary != "(none)":
        st.markdown("---")
        col_pk, col_pd = st.columns(2)
        with col_pk:
            st.markdown("**Pharmacokinetics (PK)**")
            st.caption(pk_summary if pk_summary != "(none)" else "No PK data available")
        with col_pd:
            st.markdown("**Pharmacodynamics (PD)**")
            st.caption(pd_summary if pd_summary != "(none)" else "No PD data available")

    sources = context.get("sources", {})
    if sources:
        st.markdown("---")
        st.markdown('<div id="sources"></div>', unsafe_allow_html=True)
        st.markdown("**Data Sources:**")
        source_tags = []
        if sources.get("duckdb"):
            for src in sources["duckdb"]:
                source_tags.append(f'<span class="source-badge">{src}</span>')
        if sources.get("qlever"):
            for src in sources["qlever"]:
                source_tags.append(f'<span class="source-badge">{src}</span>')
        if sources.get("openfda"):
            for src in sources["openfda"]:
                source_tags.append(f'<span class="source-badge">{src}</span>')
        if source_tags:
            st.markdown(" ".join(source_tags), unsafe_allow_html=True)

    source_status = context.get("source_status", [])
    if source_status:
        st.markdown("---")
        st.markdown("**Source Status:**")
        rows = []
        for item in source_status:
            rows.append({
                "Source": item.get("name", ""),
                "Enabled": bool(item.get("enabled")),
                "Available": bool(item.get("available")),
                "Reason": item.get("reason", ""),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.markdown('</div>', unsafe_allow_html=True)


def _render_evidence(context: Dict[str, Any]):
    drugs = context.get("drugs", {})
    a_name = drugs.get("a", {}).get("name", st.session_state.drugA)
    b_name = drugs.get("b", {}).get("name", st.session_state.drugB)
    faers = context.get("signals", {}).get("faers", {})
    mech = context.get("signals", {}).get("mechanistic", {})
    tabular = context.get("signals", {}).get("tabular", {})

    pkpd = context.get("pkpd", {}) or {}
    pk_detail = pkpd.get("pk_detail", {}) or {}
    overlaps = pk_detail.get("overlaps", {}) or {}
    pd_detail = pk_detail.get("pd_detail") or pkpd.get("pd_detail", {}) or {}

    st.markdown("---")
    st.markdown('<div id="evidence"></div>', unsafe_allow_html=True)
    st.markdown("### Evidence Dashboard")

    tabs = st.tabs(["PK/PD Analysis", "FAERS Signals", "Mechanisms & Pathways", "Risk Metrics"])

    with tabs[0]:
        st.markdown("#### Enzyme Interactions")
        enzymes = mech.get("enzymes") or {}
        if enzymes:
            enz_data = []
            for drug_key, roles in enzymes.items():
                drug_label = "Drug A" if drug_key == "a" else "Drug B"
                for role_type, enzymes_list in roles.items():
                    if enzymes_list:
                        for enzyme in enzymes_list:
                            enz_data.append({"Drug": drug_label, "Role": role_type.title(), "Enzyme": enzyme})
            if enz_data:
                df_enz = pd.DataFrame(enz_data)
                st.dataframe(df_enz, width="stretch", hide_index=True)
            else:
                st.info("No enzyme interaction data available.")
        else:
            st.info("No enzyme data found.")

        st.markdown("#### PK Overlap Signals")
        pk_overlaps = {
            "Inhibition": overlaps.get("inhibition") or [],
            "Induction": overlaps.get("induction") or [],
            "Shared Substrate": overlaps.get("shared_substrate") or [],
        }
        has_overlaps = any(pk_overlaps.values())
        if has_overlaps:
            for overlap_type, enzymes in pk_overlaps.items():
                if enzymes:
                    st.markdown(f"**{overlap_type}:**")
                    for enzyme in enzymes:
                        st.markdown(f'- <span class="drug-badge">{enzyme}</span>', unsafe_allow_html=True)
        else:
            st.info("No PK overlap detected in the retrieved data.")

        st.markdown("#### PD Overlap (Targets/Pathways)")
        pd_overlaps = {
            "Overlapping Targets": pd_detail.get("overlap_targets") or [],
            "Overlapping Pathways": pd_detail.get("overlap_pathways") or [],
            "PD Score": pd_detail.get("pd_score", 0),
        }
        if pd_overlaps["Overlapping Targets"] or pd_overlaps["Overlapping Pathways"]:
            if pd_overlaps["Overlapping Targets"]:
                st.markdown("**Overlapping Targets:**")
                for target in pd_overlaps["Overlapping Targets"][:10]:
                    st.markdown(f'- <span class="drug-badge">{target}</span>', unsafe_allow_html=True)
            if pd_overlaps["Overlapping Pathways"]:
                st.markdown("**Overlapping Pathways:**")
                for pathway in pd_overlaps["Overlapping Pathways"][:10]:
                    st.markdown(f'- <span class="drug-badge">{pathway}</span>', unsafe_allow_html=True)
            if pd_overlaps["PD Score"]:
                st.metric("PD Interaction Score", f"{pd_overlaps['PD Score']:.2f}")
        else:
            st.info("No PD overlap detected.")

    with tabs[1]:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("PRR (Pair)", _fmt_metric(tabular.get("prr")), help="Proportional Reporting Ratio")
        with col2:
            st.metric("DILI (A)", _fmt_metric(tabular.get("dili_a")))
        with col3:
            st.metric("DILI (B)", _fmt_metric(tabular.get("dili_b")))
        with col4:
            st.metric("DIQT (A)", _fmt_metric(tabular.get("diqt_a")))

        st.markdown("---")

        left, right = st.columns([2, 1])

        with left:
            a_df = _pairs_to_df(faers.get("top_reactions_a"), 10)
            if a_df is not None:
                fig_a = px.bar(a_df, x="Count", y="Reaction", orientation='h', title=f"Top Reactions: {a_name}")
                fig_a.update_layout(showlegend=False, height=330, margin=dict(l=10, r=10, t=48, b=10))
                st.plotly_chart(fig_a, width="stretch", key=f"faers_a_{a_name}_{b_name}")
            else:
                st.info(f"No FAERS data available for {a_name}.")

            b_df = _pairs_to_df(faers.get("top_reactions_b"), 10)
            if b_df is not None:
                fig_b = px.bar(b_df, x="Count", y="Reaction", orientation='h', title=f"Top Reactions: {b_name}")
                fig_b.update_layout(showlegend=False, height=330, margin=dict(l=10, r=10, t=48, b=10))
                st.plotly_chart(fig_b, width="stretch", key=f"faers_b_{a_name}_{b_name}")
            else:
                st.info(f"No FAERS data available for {b_name}.")

        with right:
            c_df = _pairs_to_df(faers.get("combo_reactions"), 15)
            if c_df is not None:
                st.markdown("#### Combination Reactions")
                st.dataframe(c_df, width="stretch", height=500, hide_index=True)
            else:
                st.info("No combination-specific reactions found in FAERS data.")

    with tabs[2]:
        st.markdown("#### Drug Identifiers")
        drugs = context.get("drugs", {})
        id_rows = []
        for label, ids in (
            (a_name, drugs.get("a", {}).get("ids", {}) or {}),
            (b_name, drugs.get("b", {}).get("ids", {}) or {}),
        ):
            if ids:
                for source, value in ids.items():
                    id_rows.append({"Drug": label, "Identifier": source, "Value": value})
            else:
                id_rows.append({"Drug": label, "Identifier": "available IDs", "Value": "Not available"})
        st.dataframe(pd.DataFrame(id_rows), width="stretch", hide_index=True)

        st.markdown("---")
        st.markdown("#### Mechanistic Evidence")
        mech_rows = []
        for section, label, values in (
            ("Targets", a_name, mech.get("targets_a", [])[:15]),
            ("Targets", b_name, mech.get("targets_b", [])[:15]),
            ("Pathways", a_name, mech.get("pathways_a", [])[:10]),
            ("Pathways", b_name, mech.get("pathways_b", [])[:10]),
            ("Common Pathways", f"{a_name} + {b_name}", mech.get("common_pathways", [])[:15]),
        ):
            for value in values or []:
                mech_rows.append({"Evidence Type": section, "Drug/Pair": label, "Value": str(value)})
        if mech_rows:
            st.dataframe(pd.DataFrame(mech_rows), width="stretch", hide_index=True)
        else:
            st.info("No target or pathway evidence was retrieved.")

    with tabs[3]:
        st.markdown("#### Tabular Risk Indicators")
        risk_metrics = {
            "PRR (Pair)": _fmt_metric(tabular.get("prr")),
            "DILI (A)": _fmt_metric(tabular.get("dili_a")),
            "DILI (B)": _fmt_metric(tabular.get("dili_b")),
            "DICT (A)": _fmt_metric(tabular.get("dict_a")),
            "DICT (B)": _fmt_metric(tabular.get("dict_b")),
            "DIQT (A)": _fmt_metric(tabular.get("diqt_a")),
            "DIQT (B)": _fmt_metric(tabular.get("diqt_b")),
        }
        cols = st.columns(3)
        for idx, (key, value) in enumerate(risk_metrics.items()):
            with cols[idx % 3]:
                st.metric(key, value)

        st.markdown("---")
        st.markdown("#### Risk Assessment Summary")
        risk_level, risk_label = _calculate_risk_level(context)
        st.markdown(
            f'<div class="risk-indicator risk-{risk_level}" style="font-size: 1.2rem; padding: 1rem;">'
            f'{risk_label}</div>',
            unsafe_allow_html=True
        )

def _render_feedback_section(context: Dict[str, Any], drugA: str, drugB: str):
    """Render user feedback section with three safety categories."""
    mode = _normalize_mode(st.session_state.get("mode", MODE_DOCTOR))
    if mode not in FEEDBACK_MODES:
        return

    pair_key = _pair_key(drugA, drugB)
    pair_str = f"{pair_key[0]}|{pair_key[1]}|{mode}"
    feedback_key = f"feedback_{pair_str}"
    
    # Check if feedback already submitted for this pair
    if st.session_state.feedback_submitted.get(pair_str, False):
        st.markdown("---")
        st.success("Thank you. Your feedback has been recorded.")
        return
    
    st.markdown("---")
    st.markdown("### Assessment Feedback")
    st.caption("Feedback is collected for clinician and research review modes.")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("Good and Safe", width="stretch", key=f"{feedback_key}_good", type="primary"):
            try:
                record_feedback(
                    drugA,
                    drugB,
                    "good",
                    user_rating=0.9,
                    context=context,
                    mode=mode,
                )
                st.session_state.feedback_submitted[pair_str] = True
                log_event("feedback.submitted", pair=(drugA, drugB), rating="good", score=0.9)
                st.rerun()
            except Exception as e:
                logger.error(f"Failed to record feedback: {e}")
                st.error("Failed to record feedback. Please try again.")
    
    with col2:
        if st.button("Moderately Safe", width="stretch", key=f"{feedback_key}_moderate"):
            try:
                record_feedback(
                    drugA,
                    drugB,
                    "neutral",
                    user_rating=0.5,
                    context=context,
                    mode=mode,
                )
                st.session_state.feedback_submitted[pair_str] = True
                log_event("feedback.submitted", pair=(drugA, drugB), rating="moderate", score=0.5)
                st.rerun()
            except Exception as e:
                logger.error(f"Failed to record feedback: {e}")
                st.error("Failed to record feedback. Please try again.")
    
    with col3:
        if st.button("Unsafe", width="stretch", key=f"{feedback_key}_unsafe", type="secondary"):
            try:
                record_feedback(
                    drugA,
                    drugB,
                    "bad",
                    user_rating=0.1,
                    context=context,
                    mode=mode,
                )
                st.session_state.feedback_submitted[pair_str] = True
                log_event("feedback.submitted", pair=(drugA, drugB), rating="unsafe", score=0.1)
                st.rerun()
            except Exception as e:
                logger.error(f"Failed to record feedback: {e}")
                st.error("Failed to record feedback. Please try again.")
    
    st.caption("Feedback stores a compact evidence snapshot for review.")


def _append_chat(role: str, text: str):
    st.session_state.chat.append({"role": role, "text": text})


def _render_chat():
    st.markdown(
        """
        <div id="ask" class="chat-panel">
          <div class="section-label">Ask AI</div>
          <h3>Follow-up Questions</h3>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not st.session_state.chat:
        st.info("Generate an initial assessment to start the analysis thread.")

    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])

    user_msg = st.chat_input("Ask a follow-up question about this drug interaction...")
    if user_msg:
        _append_chat("user", user_msg)
        _handle_followup(user_msg)


def _render_empty_state():
    st.markdown(
        """
        <div class="empty-state">
          <div class="section-label">Ready for search</div>
          <h3>Search a pair to open the interaction record</h3>
          <div class="empty-grid">
            <div class="empty-tile">
              <strong>Compound identity</strong>
              <span>PubChem and local identifiers appear first when available.</span>
            </div>
            <div class="empty-tile">
              <strong>Interaction evidence</strong>
              <span>PK/PD overlap, FAERS signals, and source status are shown together.</span>
            </div>
            <div class="empty-tile">
              <strong>Follow-up analysis</strong>
              <span>After the first assessment, ask scenario-specific questions.</span>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _handle_explain_pair():
    a, b = _pair_key(st.session_state.drugA, st.session_state.drugB)
    if not a or not b:
        st.warning("Please provide both Drug A and Drug B.")
        return

    try:
        st.session_state.chat = []
        context = _maybe_load_context(a, b)
        question = DEFAULT_QUESTION
        mode = st.session_state.mode

        with st.spinner("Generating comprehensive analysis..."):
            out = run_rag(
                a, b,
                mode=mode,
                history=[],
                use_cache_context=(st.session_state.use_ctx_cache and not st.session_state.force_refresh),
                use_cache_response=False,
                stream=True,
            )

        ctx_from_out = (out or {}).get("context") or context
        answer_block = (out or {}).get("answer", {}) or {}
        text = answer_block.get("text")

        if not text:
            st.warning("No explanation was generated.")
            log_event("explain_pair.empty_answer", pair=(a, b))
            return

        st.session_state.context_by_pair[(a, b)] = ctx_from_out
        st.session_state.active_pair = (a, b)

        _append_chat("user", question)
        _append_chat("assistant", text)

        log_event("explain_pair.done", pair=(a, b), answer_len=len(text))
    except Exception:
        logger.exception("Error in _handle_explain_pair")
        st.error("An error occurred while generating the initial explanation. See logs for details.")


def _handle_followup(user_msg: str):
    a, b = st.session_state.active_pair
    if not a or not b:
        st.warning("Start by selecting a drug pair and clicking Analyze Interaction.")
        return

    try:
        context = st.session_state.context_by_pair.get((a, b)) or _maybe_load_context(a, b)
        mode = st.session_state.mode

        history = [{"role": m["role"], "text": m["text"]} for m in st.session_state.chat if m["role"] in ("user", "assistant")]

        with st.spinner("Analyzing your question..."):
            out = generate_response(
                context, mode, history=history, stream=True,
            )

        text = out["text"]
        _append_chat("assistant", text)

        log_event("followup.done", pair=(a, b), q_len=len(user_msg), answer_len=len(text))
    except Exception:
        logger.exception("Error in _handle_followup")
        st.error("An error occurred while generating the follow-up. See logs for details.")


def _render_footer():
    st.markdown(
        f'<div class="app-footer">'
        f'<p><strong>INFERMed</strong> - Research software for drug interaction analysis</p>'
        f'<p style="font-size: 0.875rem;">This tool is for informational purposes only. '
        f'Always consult licensed clinicians for medical decisions.</p>'
        f'</div>',
        unsafe_allow_html=True
    )


# ------------------------------
# App
# ------------------------------
_init_state()
_render_header()
_sidebar()
_render_pair_inputs()

pair = _pair_key(st.session_state.drugA, st.session_state.drugB)
ctx = st.session_state.context_by_pair.get(pair)
if ctx:
    st.markdown('<div id="results" class="result-grid"></div>', unsafe_allow_html=True)
    result_col, ask_col = st.columns([1.55, 0.95], gap="large")
    with result_col:
        _render_topline(ctx)
        _render_evidence(ctx)
        _render_feedback_section(ctx, st.session_state.drugA, st.session_state.drugB)
    with ask_col:
        _render_chat()
else:
    _render_empty_state()

_render_footer()
