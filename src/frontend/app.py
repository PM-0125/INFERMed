import os
import io
import json
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, List, Tuple, Optional
from urllib.parse import quote

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Project imports (expects PYTHONPATH=.)
from src.llm.rag_pipeline import (
    get_context_cached,
    retrieve_and_normalize,
    run_rag,                 # uses get_response_cached internally when use_cache_response=True
    clear_context_cache,     # to clear disk cache for a pair
    record_feedback,         # for user feedback tracking
)
from src.llm.llm_interface import generate_response, OLLAMA_HOST  # used for follow-ups when context is already present


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
    """Inject custom CSS for ChatGPT-like interface and medical styling."""
    css = f"""
    <style>
    .main .block-container {{ max-width: 1200px; padding-top: 2rem; padding-bottom: 2rem; }}
    h1 {{ color: {MEDICAL_COLORS['primary']}; font-weight: 700; margin-bottom: 0.5rem; }}
    .stChatMessage {{ padding: 1rem; border-radius: 12px; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    .stChatMessage[data-testid="user"] {{ background-color: {MEDICAL_COLORS['light_bg']}; border-left: 4px solid {MEDICAL_COLORS['primary']}; }}
    .stChatMessage[data-testid="assistant"] {{ background-color: {MEDICAL_COLORS['card_bg']}; border-left: 4px solid {MEDICAL_COLORS['secondary']}; }}
    .drug-card {{ background: linear-gradient(135deg, {MEDICAL_COLORS['card_bg']} 0%, {MEDICAL_COLORS['light_bg']} 100%); border: 1px solid {MEDICAL_COLORS['border']}; border-radius: 12px; padding: 1.5rem; margin: 1rem 0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    .evidence-card {{ background-color: {MEDICAL_COLORS['card_bg']}; border: 1px solid {MEDICAL_COLORS['border']}; border-radius: 8px; padding: 1rem; margin: 0.5rem 0; }}
    .drug-badge {{ display: inline-block; background-color: {MEDICAL_COLORS['primary']}; color: white; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.875rem; font-weight: 500; margin: 0.25rem; }}
    .source-badge {{ display: inline-block; background-color: {MEDICAL_COLORS['light_bg']}; color: {MEDICAL_COLORS['text_primary']}; padding: 0.25rem 0.75rem; border-radius: 6px; font-size: 0.75rem; border: 1px solid {MEDICAL_COLORS['border']}; margin: 0.25rem; }}
    .risk-indicator {{ display: inline-flex; align-items: center; padding: 0.5rem 1rem; border-radius: 8px; font-weight: 600; margin: 0.5rem 0; }}
    .risk-low {{ background-color: #E8F5E9; color: #2E7D32; }}
    .risk-moderate {{ background-color: #FFF3E0; color: #E65100; }}
    .risk-high {{ background-color: #FFEBEE; color: #C62828; }}
    .drug-link {{ color: {MEDICAL_COLORS['primary']}; text-decoration: none; font-weight: 500; transition: color 0.2s; }}
    .drug-link:hover {{ color: #1565C0; text-decoration: underline; }}
    .stTextInput > div > div > input {{ border-radius: 8px; border: 1px solid {MEDICAL_COLORS['border']}; }}
    .stButton > button {{ border-radius: 8px; font-weight: 500; transition: all 0.2s; }}
    .stButton > button:hover {{ transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0,0,0,0.15); }}
    .css-1d391kg {{ background-color: {MEDICAL_COLORS['light_bg']}; }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 8px; }}
    .stTabs [data-baseweb="tab"] {{ border-radius: 8px 8px 0 0; padding: 0.75rem 1.5rem; }}
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    header {{visibility: hidden;}}
    ::-webkit-scrollbar {{ width: 8px; }}
    ::-webkit-scrollbar-track {{ background: {MEDICAL_COLORS['light_bg']}; }}
    ::-webkit-scrollbar-thumb {{ background: {MEDICAL_COLORS['border']}; border-radius: 4px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: {MEDICAL_COLORS['text_secondary']}; }}
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


def _expand_sidebar_button():
    """Add JavaScript to auto-expand sidebar and handle sidebar state."""
    # Use a more aggressive approach that works with Streamlit's iframe structure
    expand_js = """
    <script>
    (function() {
        function expandSidebar() {
            // Method 1: Try to find and click the toggle button in the parent frame
            try {
                // Streamlit runs in an iframe, so we need to access the parent
                const parentWindow = window.parent || window;
                
                // Try multiple selectors for the sidebar toggle button
                const selectors = [
                    '[data-testid="stSidebarCollapseButton"]',
                    '[aria-label*="sidebar"]',
                    'button[aria-label*="Close"]',
                    'button[aria-label*="Open"]',
                    '.css-1d391kg button',
                    '[class*="sidebar"] button'
                ];
                
                for (const selector of selectors) {
                    const btn = parentWindow.document.querySelector(selector);
                    if (btn && btn.offsetParent !== null) {
                        btn.click();
                        console.log('Clicked sidebar toggle via selector:', selector);
                        return true;
                    }
                }
                
                // Method 2: Directly manipulate sidebar
                const sidebar = parentWindow.document.querySelector('[data-testid="stSidebar"]');
                if (sidebar) {
                    sidebar.style.display = 'block';
                    sidebar.style.visibility = 'visible';
                    sidebar.setAttribute('aria-expanded', 'true');
                    sidebar.classList.remove('collapsed');
                    console.log('Manually expanded sidebar');
                    return true;
                }
                
                // Method 3: Update localStorage in parent window
                try {
                    parentWindow.localStorage.setItem('sidebarState', 'expanded');
                    parentWindow.localStorage.removeItem('sidebarCollapsed');
                    console.log('Updated localStorage');
                } catch(e) {
                    console.log('Could not update localStorage:', e);
                }
            } catch(e) {
                console.error('Error expanding sidebar:', e);
            }
            return false;
        }
        
        // Try immediately
        setTimeout(expandSidebar, 100);
        
        // Also try on various events
        ['load', 'DOMContentLoaded', 'streamlit:render'].forEach(event => {
            window.addEventListener(event, function() {
                setTimeout(expandSidebar, 200);
            });
        });
        
        // Listen for Streamlit events
        if (window.parent && window.parent.postMessage) {
            window.parent.postMessage({
                type: 'streamlit:expandSidebar'
            }, '*');
        }
    })();
    </script>
    """
    st.markdown(expand_js, unsafe_allow_html=True)


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    a = (a or "").strip()
    b = (b or "").strip()
    return a, b


def _resp_cache_key(
    pair: Tuple[str, str], question: str, mode: str, temp: float, seed: int
) -> str:
    base = f"{pair[0]}||{pair[1]}||{question.strip()}||{mode}||{temp:.4f}||{seed}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


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
    default_model = os.getenv("OLLAMA_MODEL", "gpt-oss")

    st.session_state.setdefault("mode", "Doctor")
    st.session_state.setdefault("model_name", default_model)
    st.session_state.setdefault("temperature", 0.2)
    st.session_state.setdefault("seed", 42)
    st.session_state.setdefault("use_ctx_cache", True)
    st.session_state.setdefault("use_resp_cache", True)
    st.session_state.setdefault("show_evidence", True)
    st.session_state.setdefault("show_raw", False)
    st.session_state.setdefault("force_refresh", False)

    st.session_state.setdefault("drugA", "warfarin")
    st.session_state.setdefault("drugB", "fluconazole")

    st.session_state.setdefault("context_by_pair", {})
    st.session_state.setdefault("responses", {})
    st.session_state.setdefault("chat", [])
    st.session_state.setdefault("active_pair", ("", ""))
    st.session_state.setdefault("feedback_submitted", {})  # Track feedback per pair


def _render_header():
    st.set_page_config(
        page_title=f"{APP_TITLE} — Drug Interaction Analysis",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            'Get Help': 'https://github.com/your-repo/issues',
            'Report a bug': 'https://github.com/your-repo/issues',
            'About': "INFERMed: PK/PD-aware RAG DDI Explainer\n\nIf the sidebar is hidden, look for the expander below with instructions."
        }
    )
    _inject_custom_css()

    col1, col2 = st.columns([1, 4])
    with col1:
        st.markdown(
            f'<div style="font-size: 2.5rem; color: {MEDICAL_COLORS["primary"]}; font-weight: 700;">💊</div>',
            unsafe_allow_html=True
        )
    with col2:
        st.title("INFERMed")
        st.caption("**PK/PD-aware Drug-Drug Interaction Analysis** · Powered by RAG, FAERS, and Clinical Evidence")
    

def _fetch_ollama_models() -> List[str]:
    """
    Fetch available models from Ollama API.
    Returns list of model names, or empty list if Ollama is unavailable.
    Uses session state to cache the result.
    """
    # Check if we have cached models in session state
    if "ollama_models_cache" in st.session_state:
        return st.session_state.ollama_models_cache
    
    try:
        import requests
        # Ollama API endpoint to list models
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        if response.status_code == 200:
            data = response.json()
            models = []
            for model_info in data.get("models", []):
                model_name = model_info.get("name", "")
                if model_name:
                    models.append(model_name)
            # Cache the result in session state
            st.session_state.ollama_models_cache = models
            logger.info(f"Fetched {len(models)} models from Ollama: {models}")
            return models
        else:
            logger.warning(f"Ollama API returned status {response.status_code}")
            return []
    except Exception as e:
        logger.warning(f"Failed to fetch models from Ollama: {e}")
        return []


def _sidebar():
    with st.sidebar:
        st.markdown(f'<h2 style="color: {MEDICAL_COLORS["primary"]};">⚙️ Settings</h2>', unsafe_allow_html=True)

        st.markdown("### Audience Mode")
        st.session_state.mode = st.radio(
            "Select your role",
            ["Doctor", "Patient", "Pharma"],
            index=["Doctor", "Patient", "Pharma"].index(st.session_state.mode),
            label_visibility="collapsed"
        )

        st.markdown("---")
        st.markdown("### Model Selection")
        
        # Fetch models dynamically from Ollama
        available_models = _fetch_ollama_models()
        
        # Fallback to default list if Ollama is unavailable or returns no models
        if not available_models:
            logger.info("Using fallback model list (Ollama unavailable or returned no models)")
            available_models = [
                "gpt-oss",
                "gpt-oss:latest",
                "mistral-nemo:12b",
                "merlinvn/MedicalQA-Llama-3.2-3B-Instruct",
                "Elixpo/LlamaMedicine",
            ]
        
        current_model = st.session_state.get("model_name", "gpt-oss")
        if current_model not in available_models:
            available_models.insert(0, current_model)

        # Add refresh button to reload models from Ollama
        col1, col2 = st.columns([3, 1])
        with col1:
            st.session_state.model_name = st.selectbox(
                "LLM Model",
                available_models,
                index=available_models.index(current_model) if current_model in available_models else 0,
                help="Select the Ollama model to use for generation (dynamically fetched from Ollama)"
            )
        with col2:
            if st.button("🔄", help="Refresh model list from Ollama"):
                # Clear cache to force refresh
                if "ollama_models_cache" in st.session_state:
                    del st.session_state.ollama_models_cache
                st.rerun()

        custom_model = st.text_input(
            "Or enter custom model name",
            value="" if st.session_state.model_name in available_models else st.session_state.model_name,
            placeholder="e.g., llama3:8b",
            help="Enter a custom Ollama model name (will be added to list if not present)"
        )
        if custom_model.strip():
            st.session_state.model_name = custom_model.strip()

        st.markdown("---")
        st.markdown("### Model Parameters")
        st.session_state.temperature = st.slider("Temperature (creativity)", 0.0, 1.0, float(st.session_state.temperature), 0.05)
        st.session_state.seed = st.number_input("Random seed", value=int(st.session_state.seed), step=1)

        st.markdown("---")
        st.markdown("### Cache Options")
        st.session_state.use_ctx_cache = st.checkbox("Use context cache", value=bool(st.session_state.use_ctx_cache))
        st.session_state.use_resp_cache = st.checkbox("Use response cache", value=bool(st.session_state.use_resp_cache))
        st.session_state.force_refresh = st.checkbox("Force refresh evidence (ignore cached context)", value=bool(st.session_state.force_refresh))

        st.markdown("---")
        st.markdown("### Display Options")
        st.session_state.show_evidence = st.checkbox("Show evidence panel", value=bool(st.session_state.show_evidence))
        st.session_state.show_raw = st.checkbox("Show raw context JSON", value=bool(st.session_state.show_raw))

        st.markdown("---")
        st.info("💡 Tip: enter two drug names, then click **Analyze Interaction**. Use the Debug panel below to download logs & context.")

        # --- Debug downloads panel ---
        st.markdown("### 🧪 Debug & Downloads")
        pair = _pair_key(st.session_state.get("drugA", ""), st.session_state.get("drugB", ""))
        ctx = st.session_state.context_by_pair.get(pair)

        # Download context JSON
        if ctx:
            ctx_json = json.dumps(ctx, ensure_ascii=False, indent=2)
            st.download_button(
                "⬇️ Download context.json",
                data=ctx_json.encode("utf-8"),
                file_name=f"context_{pair[0]}_{pair[1]}.json",
                mime="application/json",
            )

        # Download chat history
        chat_json = json.dumps(st.session_state.get("chat", []), ensure_ascii=False, indent=2)
        st.download_button(
            "⬇️ Download chat_history.json",
            data=chat_json.encode("utf-8"),
            file_name="chat_history.json",
            mime="application/json",
        )

        # Download latest log file
        try:
            with open(LOG_PATH, "rb") as f:
                log_bytes = f.read()
            st.download_button(
                "⬇️ Download app.log",
                data=log_bytes,
                file_name="app.log",
                mime="text/plain",
            )
        except Exception as e:
            st.caption(f"(Log file not available yet: {e})")


def _render_pair_inputs():
    st.markdown("### 🔬 Drug Pair Analysis")
    colA, colB = st.columns(2)
    with colA:
        st.session_state.drugA = st.text_input("**Drug A**", value=st.session_state.drugA, key="drugA_input", placeholder="e.g., warfarin")
    with colB:
        st.session_state.drugB = st.text_input("**Drug B**", value=st.session_state.drugB, key="drugB_input", placeholder="e.g., fluconazole")


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
        with st.spinner("🔍 Retrieving evidence from databases (RAG pipeline)…"):
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

    pd_detail = pk_detail.get("pd_detail", {}) or {}
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
        st.markdown(f'### 💊 {a_name} + {b_name}')
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
            st.markdown(f"**📊 Pharmacokinetics (PK)**")
            st.caption(pk_summary if pk_summary != "(none)" else "No PK data available")
        with col_pd:
            st.markdown(f"**⚗️ Pharmacodynamics (PD)**")
            st.caption(pd_summary if pd_summary != "(none)" else "No PD data available")

    sources = context.get("sources", {})
    if sources:
        st.markdown("---")
        st.markdown("**📚 Data Sources:**")
        source_tags = []
        if sources.get("duckdb"):
            for src in sources["duckdb"]:
                source_tags.append(f'<span class="source-badge">🗄️ {src}</span>')
        if sources.get("qlever"):
            for src in sources["qlever"]:
                source_tags.append(f'<span class="source-badge">🔗 {src}</span>')
        if sources.get("openfda"):
            for src in sources["openfda"]:
                source_tags.append(f'<span class="source-badge">📋 {src}</span>')
        if source_tags:
            st.markdown(" ".join(source_tags), unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def _render_evidence(context: Dict[str, Any]):
    if not st.session_state.show_evidence:
        return

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
    st.markdown("### 📊 Evidence Dashboard")

    tabs = st.tabs(["🔬 PK/PD Analysis", "⚠️ FAERS Signals", "🧬 Mechanisms & Pathways", "📈 Risk Metrics"])

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
                st.dataframe(df_enz, use_container_width=True, hide_index=True)
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

        a_df = _pairs_to_df(faers.get("top_reactions_a"), 10)
        if a_df is not None:
            fig_a = px.bar(a_df, x="Count", y="Reaction", orientation='h', title=f"Top Reactions: {a_name}")
            fig_a.update_layout(showlegend=False, height=400)
            st.plotly_chart(fig_a, use_container_width=True, key=f"faers_a_{a_name}_{b_name}")
        else:
            st.info(f"No FAERS data available for {a_name}.")

        b_df = _pairs_to_df(faers.get("top_reactions_b"), 10)
        if b_df is not None:
            fig_b = px.bar(b_df, x="Count", y="Reaction", orientation='h', title=f"Top Reactions: {b_name}")
            fig_b.update_layout(showlegend=False, height=400)
            st.plotly_chart(fig_b, use_container_width=True, key=f"faers_b_{a_name}_{b_name}")
        else:
            st.info(f"No FAERS data available for {b_name}.")

        with right:
            c_df = _pairs_to_df(faers.get("combo_reactions"), 15)
            if c_df is not None:
                st.markdown("#### Combination Reactions")
                st.dataframe(c_df, use_container_width=True, height=500, hide_index=True)
            else:
                st.info("No combination-specific reactions found in FAERS data.")

    with tabs[2]:
        st.markdown("#### Drug Identifiers")
        drugs = context.get("drugs", {})
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**{a_name}**")
            a_ids = drugs.get("a", {}).get("ids", {}) or {}
            st.json(a_ids)
        with col_b:
            st.markdown(f"**{b_name}**")
            b_ids = drugs.get("b", {}).get("ids", {}) or {}
            st.json(b_ids)

        st.markdown("---")
        st.markdown("#### Mechanistic Data")
        mech_data = {
            "Enzymes": mech.get("enzymes") or {},
            "Targets (A)": mech.get("targets_a", [])[:15],
            "Targets (B)": mech.get("targets_b", [])[:15],
            "Pathways (A)": mech.get("pathways_a", [])[:10],
            "Pathways (B)": mech.get("pathways_b", [])[:10],
            "Common Pathways": mech.get("common_pathways", [])[:15],
        }
        st.json(mech_data)

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

    if st.session_state.show_raw:
        st.markdown("---")
        st.markdown("#### Raw Context Data")
        st.json(context)


def _render_feedback_section(context: Dict[str, Any], drugA: str, drugB: str):
    """Render user feedback section with three safety categories."""
    pair_key = _pair_key(drugA, drugB)
    pair_str = f"{pair_key[0]}|{pair_key[1]}"
    feedback_key = f"feedback_{pair_str}"
    
    # Check if feedback already submitted for this pair
    if st.session_state.feedback_submitted.get(pair_str, False):
        st.markdown("---")
        st.success("✅ Thank you! Your feedback has been recorded and will help improve the system.")
        return
    
    st.markdown("---")
    st.markdown("### 💬 Help Us Improve")
    st.caption("Your feedback helps us improve the accuracy and safety of drug interaction assessments.")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("✅ Good and Safe", use_container_width=True, key=f"{feedback_key}_good", type="primary"):
            try:
                record_feedback(
                    drugA,
                    drugB,
                    "good",
                    user_rating=0.9,
                    context=context
                )
                st.session_state.feedback_submitted[pair_str] = True
                log_event("feedback.submitted", pair=(drugA, drugB), rating="good", score=0.9)
                st.rerun()
            except Exception as e:
                logger.error(f"Failed to record feedback: {e}")
                st.error("Failed to record feedback. Please try again.")
    
    with col2:
        if st.button("⚠️ Moderately Safe", use_container_width=True, key=f"{feedback_key}_moderate"):
            try:
                record_feedback(
                    drugA,
                    drugB,
                    "neutral",
                    user_rating=0.5,
                    context=context
                )
                st.session_state.feedback_submitted[pair_str] = True
                log_event("feedback.submitted", pair=(drugA, drugB), rating="moderate", score=0.5)
                st.rerun()
            except Exception as e:
                logger.error(f"Failed to record feedback: {e}")
                st.error("Failed to record feedback. Please try again.")
    
    with col3:
        if st.button("❌ Unsafe", use_container_width=True, key=f"{feedback_key}_unsafe", type="secondary"):
            try:
                record_feedback(
                    drugA,
                    drugB,
                    "bad",
                    user_rating=0.1,
                    context=context
                )
                st.session_state.feedback_submitted[pair_str] = True
                log_event("feedback.submitted", pair=(drugA, drugB), rating="unsafe", score=0.1)
                st.rerun()
            except Exception as e:
                logger.error(f"Failed to record feedback: {e}")
                st.error("Failed to record feedback. Please try again.")
    
    st.caption("💡 Your feedback is anonymous and helps train the system to provide better assessments.")


def _append_chat(role: str, text: str):
    st.session_state.chat.append({"role": role, "text": text})


def _render_chat():
    st.markdown("---")
    st.markdown("### 💬 Interactive Analysis")

    if not st.session_state.chat:
        st.info("👋 Start by clicking 'Analyze Interaction' to get an initial analysis, then ask follow-up questions here.")

    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])

    user_msg = st.chat_input("Ask a follow-up question about this drug interaction...")
    if user_msg:
        _append_chat("user", user_msg)
        _handle_followup(user_msg)


def _handle_explain_pair():
    a, b = _pair_key(st.session_state.drugA, st.session_state.drugB)
    if not a or not b:
        st.warning("⚠️ Please provide both Drug A and Drug B.")
        return

    try:
        context = _maybe_load_context(a, b)
        question = DEFAULT_QUESTION
        mode = st.session_state.mode
        seed = int(st.session_state.seed)
        temp = float(st.session_state.temperature)

        cache_key = _resp_cache_key((a, b), question, mode, temp, seed)
        if st.session_state.use_resp_cache and cache_key in st.session_state.responses:
            text = st.session_state.responses[cache_key]
            _append_chat("user", question)
            _append_chat("assistant", text)
            log_event("explain_pair.cache_hit", pair=(a, b))
            return

        with st.spinner("🤖 Generating comprehensive analysis…"):
            out = run_rag(
                a, b,
                mode=mode,
                seed=seed,
                temperature=temp,
                history=[],
                use_cache_context=(st.session_state.use_ctx_cache and not st.session_state.force_refresh),
                use_cache_response=st.session_state.use_resp_cache,
                model_name=st.session_state.model_name,
            )

        ctx_from_out = (out or {}).get("context") or context
        answer_block = (out or {}).get("answer", {}) or {}
        text = answer_block.get("text")

        if not text:
            st.warning("No explanation was generated.")
            with st.expander("Debug: Evidence snapshot"):
                st.json({
                    "pk_summary": ctx_from_out.get("pkpd", {}).get("pk_summary"),
                    "pd_summary": ctx_from_out.get("pkpd", {}).get("pd_summary"),
                    "qlever_contributed": ctx_from_out.get("meta", {}).get("qlever_contributed"),
                    "faers_top_a": ctx_from_out.get("signals", {}).get("faers", {}).get("top_reactions_a", [])[:5],
                    "faers_top_b": ctx_from_out.get("signals", {}).get("faers", {}).get("top_reactions_b", [])[:5],
                    "caveats": ctx_from_out.get("caveats", []),
                })
            log_event("explain_pair.empty_answer", pair=(a, b))
            return

        st.session_state.context_by_pair[(a, b)] = ctx_from_out
        st.session_state.active_pair = (a, b)

        if st.session_state.use_resp_cache:
            st.session_state.responses[cache_key] = text

        _append_chat("user", question)
        _append_chat("assistant", text)

        log_event("explain_pair.done", pair=(a, b), answer_len=len(text))
    except Exception:
        logger.exception("Error in _handle_explain_pair")
        st.error("An error occurred while generating the initial explanation. See logs for details.")


def _handle_followup(user_msg: str):
    a, b = st.session_state.active_pair
    if not a or not b:
        st.warning("⚠️ Start by selecting a drug pair and clicking **Analyze Interaction**.")
        return

    try:
        context = st.session_state.context_by_pair.get((a, b)) or _maybe_load_context(a, b)
        mode = st.session_state.mode
        seed = int(st.session_state.seed)
        temp = float(st.session_state.temperature)

        cache_key = _resp_cache_key((a, b), user_msg, mode, temp, seed)
        if st.session_state.use_resp_cache and cache_key in st.session_state.responses:
            _append_chat("assistant", st.session_state.responses[cache_key])
            log_event("followup.cache_hit", pair=(a, b))
            return

        history = [{"role": m["role"], "text": m["text"]} for m in st.session_state.chat if m["role"] in ("user", "assistant")]

        with st.spinner("🤔 Analyzing your question…"):
            out = generate_response(
                context, mode, seed=seed, temperature=temp, history=history,
                model_name=st.session_state.model_name
            )

        text = out["text"]
        if st.session_state.use_resp_cache:
            st.session_state.responses[cache_key] = text
        _append_chat("assistant", text)

        log_event("followup.done", pair=(a, b), q_len=len(user_msg), answer_len=len(text))
    except Exception:
        logger.exception("Error in _handle_followup")
        st.error("An error occurred while generating the follow-up. See logs for details.")


def _render_footer():
    st.markdown("---")
    st.markdown(
        f'<div style="text-align: center; color: {MEDICAL_COLORS["text_secondary"]}; padding: 2rem;">'
        f'<p><strong>© INFERMed</strong> — Research software for drug interaction analysis</p>'
        f'<p style="font-size: 0.875rem;">⚠️ This tool is for informational purposes only. '
        f'Always consult licensed clinicians for medical decisions.</p>'
        f'</div>',
        unsafe_allow_html=True
    )


# ------------------------------
# App
# ------------------------------
_init_state()
_render_header()  # This includes the expand button now
_expand_sidebar_button()  # Add JavaScript to auto-expand sidebar
_sidebar()
_render_pair_inputs()

col_btn1, col_btn2, col_btn3 = st.columns([2, 1, 1])
with col_btn1:
    if st.button("🔬 Analyze Interaction", type="primary", use_container_width=True, key="btn_explain_pair"):
        _handle_explain_pair()
with col_btn2:
    if st.button("🗑️ Clear Chat", use_container_width=True, key="btn_clear_chat"):
        st.session_state.chat.clear()
        st.toast("Chat cleared.", icon="✅")
with col_btn3:
    if st.button("🔄 Refresh Evidence", use_container_width=True, key="btn_refresh"):
        pair = _pair_key(st.session_state.drugA, st.session_state.drugB)
        if pair in st.session_state.context_by_pair:
            del st.session_state.context_by_pair[pair]
        try:
            cleared = clear_context_cache(*pair)
            if cleared:
                st.toast("Disk cache for this pair cleared.", icon="🧹")
        except Exception:
            logger.exception("Error clearing context cache")
        st.toast("Evidence cache cleared. Click 'Analyze Interaction' to refresh.", icon="🔄")
        log_event("refresh_evidence.clicked", pair=pair)

pair = _pair_key(st.session_state.drugA, st.session_state.drugB)
ctx = st.session_state.context_by_pair.get(pair)
if ctx:
    _render_topline(ctx)
    _render_evidence(ctx)
    _render_feedback_section(ctx, st.session_state.drugA, st.session_state.drugB)

_render_chat()
_log_size = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
st.caption(f"🗂️ Log file: `{LOG_PATH}` ({_log_size} bytes)")
_render_footer()
