import os
import hashlib
from typing import Dict, Any, List, Tuple

import streamlit as st
import pandas as pd
import plotly.express as px

# Project imports (expects PYTHONPATH=.)
from src.llm.rag_pipeline import (
    get_context_cached,
    retrieve_and_normalize,
    run_rag,  # uses get_response_cached internally when use_cache_response=True
)
from src.llm.llm_interface import generate_response  # used for follow-ups when context is already present

# ------------------------------
# Small helpers
# ------------------------------
APP_TITLE = "INFERMed"
DEFAULT_QUESTION = "Please explain the interaction, monitoring, and actions."


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    a = (a or "").strip()
    b = (b or "").strip()
    # keep original order for display, but cache uses unordered hash elsewhere
    return a, b


def _resp_cache_key(
    pair: Tuple[str, str], question: str, mode: str, temp: float, seed: int
) -> str:
    # Include question + doctor/patient/pharma mode + decoding settings so follow-ups are unique
    base = f"{pair[0]}||{pair[1]}||{question.strip()}||{mode}||{temp:.4f}||{seed}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _init_state():
    st.session_state.setdefault("mode", "Doctor")
    st.session_state.setdefault("temperature", 0.2)
    st.session_state.setdefault("seed", 42)
    st.session_state.setdefault("use_ctx_cache", True)
    st.session_state.setdefault("use_resp_cache", True)
    st.session_state.setdefault("show_evidence", True)
    st.session_state.setdefault("show_raw", False)

    st.session_state.setdefault("drugA", "warfarin")
    st.session_state.setdefault("drugB", "fluconazole")

    # app caches
    st.session_state.setdefault("context_by_pair", {})  # (a,b) -> context dict
    st.session_state.setdefault("responses", {})  # resp_cache_key -> text
    st.session_state.setdefault("chat", [])  # list[{"role": "user"/"assistant", "text": str}]
    st.session_state.setdefault("active_pair", ("", ""))


def _render_header():
    st.set_page_config(
        page_title=f"{APP_TITLE} — RAG · PK/PD · FAERS", layout="wide"
    )
    st.title("INFERMed")
    st.caption("PK/PD-aware Retrieval-Augmented DDI Explainer  —  RAG · PK/PD · FAERS")


def _sidebar():
    with st.sidebar:
        st.subheader("Settings")
        st.session_state.mode = st.radio(
            "Audience mode",
            ["Doctor", "Patient", "Pharma"],
            index=["Doctor", "Patient", "Pharma"].index(st.session_state.mode),
        )
        st.session_state.temperature = st.slider(
            "Creativity (temperature)",
            0.0,
            1.0,
            float(st.session_state.temperature),
            0.05,
        )
        st.session_state.seed = st.number_input(
            "Seed (for determinism)", value=int(st.session_state.seed), step=1
        )

        st.session_state.use_ctx_cache = st.checkbox(
            "Use context cache", value=bool(st.session_state.use_ctx_cache)
        )
        st.session_state.use_resp_cache = st.checkbox(
            "Use response cache", value=bool(st.session_state.use_resp_cache)
        )
        st.session_state.show_evidence = st.checkbox(
            "Show evidence panel", value=bool(st.session_state.show_evidence)
        )
        st.session_state.show_raw = st.checkbox(
            "Show raw context JSON", value=bool(st.session_state.show_raw)
        )

        st.markdown("---")
        st.caption(
            "Tip: For follow-ups on the same pair, just ask below — context is preserved during this session."
        )


def _render_pair_inputs():
    colA, colB = st.columns(2)
    with colA:
        st.session_state.drugA = st.text_input(
            "Drug A", value=st.session_state.drugA, key="drugA_input"
        )
    with colB:
        st.session_state.drugB = st.text_input(
            "Drug B", value=st.session_state.drugB, key="drugB_input"
        )


def _maybe_load_context(a: str, b: str) -> Dict[str, Any]:
    """
    Ensure we have a normalized context in memory for this pair.
    Uses get_context_cached() so retrieval + synthesis are reused across runs.
    """
    use_cache = st.session_state.use_ctx_cache
    pair = _pair_key(a, b)
    st.session_state.active_pair = pair

    if use_cache and pair in st.session_state.context_by_pair:
        return st.session_state.context_by_pair[pair]

    # build/refresh
    with st.spinner("Retrieving evidence and normalizing (RAG)…"):
        ctx, _ = (
            get_context_cached(a, b)
            if use_cache
            else (retrieve_and_normalize(a, b), None)
        )
        st.session_state.context_by_pair[pair] = ctx
        return ctx


def _render_topline(context: Dict[str, Any]):
    drugs = context.get("drugs", {})
    a_name = drugs.get("a", {}).get("name") or st.session_state.drugA
    b_name = drugs.get("b", {}).get("name") or st.session_state.drugB

    # Tiny topline card
    st.write(f"**{a_name} + {b_name}**")
    pkpd = context.get("pkpd", {}) or {}
    st.caption(
        f'PK: {pkpd.get("pk_summary", "(none)")}  \n'
        f'PD: {pkpd.get("pd_summary", "(none)")}'
    )

    # Sources badges
    sources = context.get("sources", {})
    if sources:
        tags = []
        if sources.get("duckdb"):
            tags.append("duckdb: " + ", ".join(sources["duckdb"]))
        if sources.get("qlever"):
            tags.append("qlever: " + ", ".join(sources["qlever"]))
        if sources.get("openfda"):
            tags.append("openfda: " + ", ".join(sources["openfda"]))
        if tags:
            st.caption(" · ".join(tags))


def _render_evidence(context: Dict[str, Any]):
    if not st.session_state.show_evidence:
        return

    drugs = context.get("drugs", {})
    a_name = drugs.get("a", {}).get("name", st.session_state.drugA)
    b_name = drugs.get("b", {}).get("name", st.session_state.drugB)
    faers = context.get("signals", {}).get("faers", {})
    mech = context.get("signals", {}).get("mechanistic", {})

    # PK/PD summary structure as produced by pkpd_utils.summarize_pkpd_risk
    pkpd = context.get("pkpd", {}) or {}
    pk_detail = pkpd.get("pk_detail", {}) or {}
    overlaps = pk_detail.get("overlaps", {}) or {}
    pd_detail = pk_detail.get("pd_detail") or pkpd.get("pd_detail", {}) or {}

    st.subheader("Evidence (current pair)")

    tabs = st.tabs(["PK/PD", "FAERS", "IDs & Pathways"])

    # --- PK/PD tab ---
    with tabs[0]:
        st.markdown("**Enzyme roles**")
        st.json(mech.get("enzymes") or {})

        st.markdown("**PK overlap signals**")
        st.json(
            {
                "inhibition": overlaps.get("inhibition") or [],
                "induction": overlaps.get("induction") or [],
                "shared_substrate": overlaps.get("shared_substrate") or [],
            }
        )

        st.markdown("**PD overlap (targets/pathways)**")
        st.json(
            {
                "overlap_targets": pd_detail.get("overlap_targets") or [],
                "overlap_pathways": pd_detail.get("overlap_pathways") or [],
                "pd_score": pd_detail.get("pd_score", 0),
            }
        )

    # --- FAERS tab ---
    with tabs[1]:
        left, right = st.columns([2, 1])
        # Top reactions — A
        a_top = faers.get("top_reactions_a") or []
        if a_top:
            df_a = pd.DataFrame(a_top, columns=["reaction", "count"])
            fig_a = px.bar(
                df_a.head(5),
                x="reaction",
                y="count",
                title=f"Top 5 Reactions for {a_name}",
            )
            st.plotly_chart(
                fig_a, use_container_width=True, key=f"faers_a_{a_name}_{b_name}"
            )
        else:
            st.info(f"No FAERS data for {a_name}.")

        # Top reactions — B
        b_top = faers.get("top_reactions_b") or []
        if b_top:
            df_b = pd.DataFrame(b_top, columns=["reaction", "count"])
            fig_b = px.bar(
                df_b.head(5),
                x="reaction",
                y="count",
                title=f"Top 5 Reactions for {b_name}",
            )
            st.plotly_chart(
                fig_b, use_container_width=True, key=f"faers_b_{a_name}_{b_name}"
            )
        else:
            st.info(f"No FAERS data for {b_name}.")

        # Combo table on the side
        with right:
            combo = faers.get("combo_reactions") or []
            if combo:
                df_c = pd.DataFrame(combo, columns=["reaction", "count"]).head(10)
                st.dataframe(df_c, use_container_width=True, height=380)
            else:
                st.caption("No combo reactions found.")

    # --- IDs & Pathways tab ---
    with tabs[2]:
        st.markdown("**Mechanistic (normalized)**")
        st.json(
            {
                "enzymes": mech.get("enzymes") or {},
                "targets_a": mech.get("targets_a") or [],
                "targets_b": mech.get("targets_b") or [],
                "pathways_a": mech.get("pathways_a") or [],
                "pathways_b": mech.get("pathways_b") or [],
                "common_pathways": mech.get("common_pathways") or [],
            }
        )

    # Optional raw JSON
    if st.session_state.show_raw:
        st.markdown("---")
        st.json(context)


def _append_chat(role: str, text: str):
    st.session_state.chat.append({"role": role, "text": text})


def _render_chat():
    st.subheader("Chat")

    # stream existing thread
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])

    # follow-up input
    user_msg = st.chat_input(
        "Ask about this drug pair, e.g., 'Is dose adjustment needed?'"
    )
    if user_msg:
        _append_chat("user", user_msg)
        _handle_followup(user_msg)


def _handle_explain_pair():
    """
    First-turn explanation for the current drug pair.
    Uses run_rag(), which in turn can cache both context and response to disk.
    """
    a, b = _pair_key(st.session_state.drugA, st.session_state.drugB)
    if not a or not b:
        st.warning("Please provide both Drug A and Drug B.")
        return

    # Ensure we have context in memory for the evidence panel (uses get_context_cached)
    context = _maybe_load_context(a, b)

    # First-turn question is fixed (can later be made editable)
    question = DEFAULT_QUESTION
    mode = st.session_state.mode
    seed = int(st.session_state.seed)
    temp = float(st.session_state.temperature)

    # In-memory response cache key (for this browser session only)
    cache_key = _resp_cache_key((a, b), question, mode, temp, seed)
    if st.session_state.use_resp_cache and cache_key in st.session_state.responses:
        text = st.session_state.responses[cache_key]
        _append_chat("user", question)
        _append_chat("assistant", text)
        return

    with st.spinner("Generating explanation…"):
        # Delegate to RAG pipeline; enable disk response cache if toggle is on
        out = run_rag(
            a,
            b,
            mode=mode,
            seed=seed,
            temperature=temp,
            history=[],  # no prior turns on first explanation
            use_cache_context=st.session_state.use_ctx_cache,
            use_cache_response=st.session_state.use_resp_cache,
        )

    # run_rag returns {"context": context, "answer": {...}}
    ctx_from_out = (out or {}).get("context") or context
    answer_block = (out or {}).get("answer", {}) or {}
    text = answer_block.get("text")

    if not text:
        st.error(
            "No explanation was generated. Please check the backend or logs for errors.\n\n"
            f"Debug info: {out}"
        )
        return

    # Refresh in-memory context for this pair from run_rag output
    st.session_state.context_by_pair[(a, b)] = ctx_from_out
    st.session_state.active_pair = (a, b)

    # Save to in-memory session cache
    if st.session_state.use_resp_cache:
        st.session_state.responses[cache_key] = text

    _append_chat("user", question)
    _append_chat("assistant", text)


def _handle_followup(user_msg: str):
    """Reuse the current context; call the LLM with history (no disk response cache yet)."""
    a, b = st.session_state.active_pair
    if not a or not b:
        st.warning("Start by selecting a drug pair and click **Explain this pair**.")
        return

    context = st.session_state.context_by_pair.get((a, b))
    if not context:
        context = _maybe_load_context(a, b)

    mode = st.session_state.mode
    seed = int(st.session_state.seed)
    temp = float(st.session_state.temperature)

    # cache key for follow-up (includes the question)
    cache_key = _resp_cache_key((a, b), user_msg, mode, temp, seed)
    if st.session_state.use_resp_cache and cache_key in st.session_state.responses:
        _append_chat("assistant", st.session_state.responses[cache_key])
        return

    # Build history in the minimal schema our llm_interface expects
    history = [
        {"role": m["role"], "text": m["text"]}
        for m in st.session_state.chat
        if m["role"] in ("user", "assistant")
    ]

    with st.spinner("Thinking…"):
        # call the LLM directly with the already-built context
        out = generate_response(
            context, mode, seed=seed, temperature=temp, history=history
        )

    text = out["text"]
    if st.session_state.use_resp_cache:
        st.session_state.responses[cache_key] = text
    _append_chat("assistant", text)


def _render_footer():
    st.markdown(
        "© **INFERMed** — This is research software; final decisions rest with licensed clinicians.",
        help="Always use clinical judgment and approved labeling.",
    )


# ------------------------------
# App
# ------------------------------
_init_state()
_render_header()
_sidebar()
_render_pair_inputs()

col_btn1, col_btn2 = st.columns([1, 1])
with col_btn1:
    if st.button(
        "Explain this pair",
        type="primary",
        use_container_width=True,
        key="btn_explain_pair",
    ):
        _handle_explain_pair()
with col_btn2:
    if st.button("Clear chat", use_container_width=True, key="btn_clear_chat"):
        st.session_state.chat.clear()
        # keep context around so evidence panel remains
        st.toast("Chat cleared.", icon="✅")

# Show topline + evidence for the active pair
pair = _pair_key(st.session_state.drugA, st.session_state.drugB)
ctx = st.session_state.context_by_pair.get(pair)
if ctx:
    _render_topline(ctx)
    _render_evidence(ctx)

# Chat area (includes follow-up input and answers)
_render_chat()

_render_footer()
