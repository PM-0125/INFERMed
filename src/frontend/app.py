# src/frontend/app.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

# INFERMed modules
from src.llm.rag_pipeline import run_rag, get_context_cached
from src.retrieval.openfda_api import OpenFDAClient

# ---------- Page & Theme ----------
st.set_page_config(
    page_title="INFERMed — PK/PD-aware DDI",
    page_icon="💊",
    layout="wide",
)

# Subtle DrugBank-like / clinical look
st.markdown(
    """
    <style>
      :root {
        --ink: #0b1736;      /* deep navy */
        --ink-2: #2b3a67;    /* muted blue */
        --brand: #0ea5e9;    /* cyan/sky */
        --brand-2: #22d3ee;  /* teal */
        --bg: #f7fbff;       /* soft medical white-blue */
        --card: #ffffff;
        --muted: #6b7280;
      }
      .stApp { background: var(--bg); }
      .app-header { font-size: 1.6rem; font-weight: 700; color: var(--ink); }
      .app-sub { color: var(--muted); margin-top: -0.25rem; }
      .pill {
        display:inline-block; padding: 0.15rem 0.6rem; border-radius: 1rem;
        background: rgba(14,165,233,.1); color: var(--ink-2); font-size: 0.8rem; margin-left: .5rem;
      }
      .source-badge {
        display:inline-block; margin-right:.5rem; padding:.1rem .5rem; border-radius:.6rem;
        background:#eef6ff; color:#1e40af; font-size:.78rem;
        border:1px solid #dbeafe;
      }
      .context-card { background: var(--card); padding: 0.9rem 1rem; border-radius: 0.75rem; border: 1px solid #e5e7eb; }
      .small { color: var(--muted); font-size: .85rem; }
      .tight { margin-top: .25rem; }
      .badge-ok { color: #065f46; background: #ecfdf5; border:1px solid #d1fae5; }
      .badge-warn { color: #78350f; background: #fffbeb; border:1px solid #fef3c7; }
      .footer-note { color: var(--muted); font-size: .8rem; }
      /* Make chat area a bit wider visually */
      [data-testid="stChatMessage"] { max-width: 1100px; margin-left: auto; margin-right: auto; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="app-header">INFERMed</div>', unsafe_allow_html=True)
st.markdown('<div class="app-sub">PK/PD-aware Retrieval-Augmented DDI Explainer<span class="pill">RAG · PK/PD · FAERS</span></div>', unsafe_allow_html=True)
st.divider()

# ---------- Sidebar Controls ----------
with st.sidebar:
    st.markdown("### Settings")
    mode = st.radio("Audience mode", ["Doctor", "Patient", "Pharma"], index=0, help="Changes tone and detail.")
    temperature = st.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.05)
    seed = st.number_input("Seed (for determinism)", value=42, step=1)
    use_cache_context = st.checkbox("Use context cache", value=True, help="Re-uses retrieval/PKPD synthesis for the same pair.")
    use_cache_response = st.checkbox("Use response cache", value=False, help="Caches LLM output by context+params.")
    show_evidence = st.checkbox("Show evidence panel", value=True)
    show_raw_json = st.checkbox("Show raw context JSON", value=False)
    st.markdown("---")
    st.caption("Tip: For follow-up questions on the same pair, just ask below — context is preserved in this session.")

# ---------- Session State ----------
MessageList = List[Dict[str, str]]
if "messages" not in st.session_state:
    messages: MessageList = []
    st.session_state.messages = messages

if "active_pair" not in st.session_state:
    st.session_state.active_pair = None  # ("drugA|drugB")

if "context_cache" not in st.session_state:
    context_cache: Dict[str, Dict[str, Any]]
    st.session_state.context_cache = {}

# ---------- Drug Pair Input ----------
colA, colB = st.columns([1, 1])
with colA:
    drug_a = st.text_input("Drug A", value="warfarin").strip()
with colB:
    drug_b = st.text_input("Drug B", value="fluconazole").strip()

pair_key = "|".join(sorted([drug_a.lower(), drug_b.lower()]))

# ---------- Ensure OpenFDA plotting client ----------
cache_dir = Path(__file__).parents[2] / "data" / "openfda"
client = OpenFDAClient(cache_dir=str(cache_dir))

# ---------- Chat Area ----------
st.markdown("#### Chat")
chat_container = st.container()

# Render history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------- Input composer ----------
user_text = st.chat_input("Ask about this drug pair, e.g., 'Is dose adjustment needed?'")

def _summarize_sources(src: Dict[str, Any]) -> str:
    if not src: return ""
    chunks = []
    for k in ("duckdb", "qlever", "openfda"):
        vals = src.get(k, []) or []
        label = k
        if vals:
            chunks.append(f'<span class="source-badge">{label}: {", ".join(vals)}</span>')
        else:
            chunks.append(f'<span class="source-badge">{label}: (none)</span>')
    return " ".join(chunks)

def _render_context_topline(ctx: Dict[str, Any]):
    drugs = ctx.get("drugs", {})
    a = (drugs.get("a", {}) or {}).get("name", "Drug A")
    b = (drugs.get("b", {}) or {}).get("name", "Drug B")
    pkpd = ctx.get("pkpd") or {}
    pk = pkpd.get("pk_summary", "No strong PK overlap detected")
    pd = pkpd.get("pd_summary", "No obvious PD overlap")
    st.markdown(
        f"""
        <div class="context-card">
        <b>{a}</b> + <b>{b}</b><br/>
        <span class="small tight">PK: {pk}</span><br/>
        <span class="small tight">PD: {pd}</span><br/>
        <div class="tight">{_summarize_sources(ctx.get("sources", {}))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def _render_evidence_tabs(ctx: Dict[str, Any]):
    tabs = st.tabs(["PK/PD", "FAERS", "IDs & Pathways"])
    mech = (ctx.get("signals", {}) or {}).get("mechanistic", {}) or {}
    tabular = (ctx.get("signals", {}) or {}).get("tabular", {}) or {}
    faers = (ctx.get("signals", {}) or {}).get("faers", {}) or {}

    with tabs[0]:
        roles = (ctx.get("pkpd") or {}).get("pk_detail", {}).get("roles", {})
        overlaps = (ctx.get("pkpd") or {}).get("pk_detail", {}).get("overlaps", {})
        st.write("**Enzyme roles**")
        st.json(roles, expanded=False)
        st.write("**PK overlap signals**")
        st.json(overlaps, expanded=False)
        st.write("**PD overlap (targets/pathways)**")
        st.json((ctx.get("pkpd") or {}).get("pd_detail", {}), expanded=False)

    with tabs[1]:
        left, right = st.columns([1, 1])
        with left:
            st.write("Top reactions — Drug A")
            if faers.get("top_reactions_a"):
                st.plotly_chart(client.plot_top_reactions((ctx["drugs"]["a"]["name"])), use_container_width=True)
            else:
                st.info("No FAERS data for Drug A.")
            st.write("Top reactions — Drug B")
            if faers.get("top_reactions_b"):
                st.plotly_chart(client.plot_top_reactions((ctx["drugs"]["b"]["name"])), use_container_width=True)
            else:
                st.info("No FAERS data for Drug B.")
        with right:
            st.write("Combination reactions")
            if faers.get("combo_reactions"):
                # plot combination if your client supports; else, display table
                st.table(faers.get("combo_reactions"))
            else:
                st.info("No combination FAERS entries.")

    with tabs[2]:
        st.write("**Tabular risk flags**")
        st.json(tabular, expanded=False)
        st.write("**Mechanistic (normalized)**")
        st.json(mech, expanded=False)

# ---------- Handle a new user message ----------
if user_text:
    # Add user message to history
    st.session_state.messages.append({"role": "user", "content": user_text})

    # Build lightweight history for the LLM (role,text only)
    history = [{"role": m["role"], "text": m["content"]} for m in st.session_state.messages[:-1]]

    # Run the RAG (context is cached on pair by default)
    with st.chat_message("assistant"):
        with st.spinner("Analyzing interactions..."):
            result = run_rag(
                drug_a,
                drug_b,
                mode=mode,
                seed=int(seed),
                temperature=float(temperature),
                history=history,
                use_cache_context=use_cache_context,
                use_cache_response=use_cache_response,
            )
            answer = result["answer"]
            context = result["context"]

            # Stash context in session by pair for later evidence rendering
            st.session_state.context_cache[pair_key] = context
            st.session_state.active_pair = pair_key

            st.markdown(answer["text"])

            st.caption(
                f"Model: {answer.get('meta',{}).get('model','unknown')} · "
                f"Temp: {answer.get('meta',{}).get('temperature','?')} · "
                f"Seed: {answer.get('meta',{}).get('seed','?')}"
            )

            if show_evidence:
                st.markdown("#### Evidence")
                _render_context_topline(context)
                _render_evidence_tabs(context)

            if show_raw_json:
                st.markdown("#### Raw context JSON")
                st.json(context, expanded=False)

    # Append assistant message to history
    st.session_state.messages.append({"role": "assistant", "content": answer["text"]})

# ---------- First-load “Explain” button ----------
col1, col2, col3 = st.columns([1, 1, 3])
with col1:
    if st.button("Explain this pair", type="primary", use_container_width=True):
        starter = "Please explain the interaction, monitoring, and actions."
        st.session_state.messages.append({"role": "user", "content": starter})
        st.rerun()

with col2:
    if st.button("Clear chat", use_container_width=True):
        st.session_state.messages.clear()
        st.session_state.active_pair = None
        st.rerun()

# ---------- Context panel (if exists) ----------
if show_evidence and st.session_state.active_pair:
    st.markdown("### Evidence (current pair)")
    ctx = st.session_state.context_cache.get(st.session_state.active_pair)
    if ctx:
        _render_context_topline(ctx)
        _render_evidence_tabs(ctx)

st.markdown(
    '<div class="footer-note">© INFERMed — This is research software; final decisions rest with licensed clinicians.</div>',
    unsafe_allow_html=True,
)
