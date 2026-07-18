from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from src.config.settings import get_settings

# Sensitive local .env files are opt-in. Codex/test runs should use process
# environment variables or example env files rather than reading secrets.
if os.getenv("INFERMED_LOAD_DOTENV", "").strip().lower() in {"1", "true", "yes", "on"}:
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        load_dotenv(env_path, override=False)
    except ImportError:
        pass

# ========== Configuration ==========
MODEL_NAME = os.getenv("OLLAMA_MODEL", "gpt-oss")
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
        "- Clinical label/reference context: {{CLINICAL_REFERENCE}}\n"
        "- Research/API enrichment context: {{RESEARCH_ENRICHMENT}}\n"
        "- Risk flags (PRR, DILI, DICT, DIQT): {{RISK_FLAGS}}\n"
        "- Mechanistic evidence (enzymes/targets/pathways, possibly with PDB-like IDs): {{EVIDENCE_TABLE}}\n"
        "- Sources: {{SOURCES}}\n"
        "- Limitations / caveats: {{CAVEATS}}\n"
        "- Conversation history: {{HISTORY}}\n"
        "- Raw context JSON: {{RAW_CONTEXT_JSON}}\n\n"
        "RESPONSE STYLE:\n"
        "Use exactly these markdown section headings:\n"
        "## Bottom Line\n"
        "## Interaction Mechanism\n"
        "## Clinical Concern\n"
        "## Monitoring & Actions\n"
        "## Evidence Limitations\n"
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
        "- Clinical label/reference context: {{CLINICAL_REFERENCE}}\n"
        "- Research/API enrichment context: {{RESEARCH_ENRICHMENT}}\n"
        "- Risk flags: {{RISK_FLAGS}}\n"
        "- Sources: {{SOURCES}}\n"
        "- Prior discussion: {{HISTORY}}\n"
        "- Caveats: {{CAVEATS}}\n\n"
        "RESPONSE STYLE:\n"
        "Use exactly these markdown section headings:\n"
        "## Bottom Line\n"
        "## Interaction Mechanism\n"
        "## Clinical Concern\n"
        "## Monitoring & Actions\n"
        "## Evidence Limitations\n"
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
        "- Clinical label/reference context: {{CLINICAL_REFERENCE}}\n"
        "- Research/API enrichment context: {{RESEARCH_ENRICHMENT}}\n"
        "- Risk flags: {{RISK_FLAGS}}\n"
        "- Mechanistic evidence snapshot: {{EVIDENCE_TABLE}}\n"
        "- Sources: {{SOURCES}}\n"
        "- Caveats: {{CAVEATS}}\n"
        "- History: {{HISTORY}}\n"
        "- Raw context JSON: {{RAW_CONTEXT_JSON}}\n\n"
        "RESPONSE STYLE:\n"
        "Use exactly these markdown section headings:\n"
        "## Bottom Line\n"
        "## Interaction Mechanism\n"
        "## Clinical Concern\n"
        "## Monitoring & Actions\n"
        "## Evidence Limitations\n"
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
    stream: Optional[bool] = None,
) -> Dict[str, Any]:
    prompt = build_prompt(context or {}, mode, history=history)
    settings = get_settings()

    selected_model = model_name or settings.ollama_model or MODEL_NAME
    num_predict = int(max_tokens) if (max_tokens is not None) else int(settings.ollama_num_predict)

    if settings.llm_provider == "mock":
        return _mock_response(context or {}, mode, selected_model="mock", temperature=temperature, seed=seed)

    if settings.llm_provider == "nvidia":
        return _generate_nvidia_response(
            prompt,
            mode,
            model_name=model_name or settings.nvidia_model,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            max_tokens=max_tokens if max_tokens is not None else settings.llm_max_tokens,
            stream=settings.llm_stream if stream is None else bool(stream),
            seed=seed,
        )

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
        r = requests.post(f"{settings.ollama_host}/api/generate", json=payload, timeout=settings.ollama_timeout_s)
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


def generate_followup_response(
    context: Dict[str, Any],
    mode: str,
    *,
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    prior_assessment: Optional[str] = None,
    seed: Optional[int] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    model_name: Optional[str] = None,
    stream: Optional[bool] = None,
) -> Dict[str, Any]:
    """Generate a compact answer to a scenario-specific follow-up question."""
    prompt = build_followup_prompt(
        context or {},
        mode,
        question=question,
        history=history,
        prior_assessment=prior_assessment,
    )
    settings = get_settings()

    selected_model = model_name or settings.ollama_model or MODEL_NAME
    num_predict = int(max_tokens) if (max_tokens is not None) else int(settings.ollama_num_predict)

    if settings.llm_provider == "mock":
        return _mock_followup_response(
            context or {},
            mode,
            question=question,
            selected_model="mock",
            temperature=temperature,
            seed=seed,
        )

    if settings.llm_provider == "nvidia":
        followup_tokens = max_tokens
        if followup_tokens is None and settings.llm_max_tokens:
            followup_tokens = min(int(settings.llm_max_tokens), 1800)
        return _generate_nvidia_response(
            prompt,
            mode,
            model_name=model_name or settings.nvidia_model,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            max_tokens=followup_tokens,
            stream=settings.llm_stream if stream is None else bool(stream),
            seed=seed,
        )

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
        r = requests.post(f"{settings.ollama_host}/api/generate", json=payload, timeout=settings.ollama_timeout_s)
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
                "followup": True,
                "ts": int(time.time()),
            },
        }
    except Exception as e:
        return _fallback(f"Ollama exception: {e}")


def _mock_response(
    context: Dict[str, Any],
    mode: str,
    *,
    selected_model: str,
    temperature: float,
    seed: Optional[int],
) -> Dict[str, Any]:
    drugs = context.get("drugs", {}) or {}
    drug_a = (drugs.get("a", {}) or {}).get("name", "Drug A")
    drug_b = (drugs.get("b", {}) or {}).get("name", "Drug B")
    pkpd = context.get("pkpd", {}) or {}
    pk_summary = pkpd.get("pk_summary") or "No PK mechanism was identified in the provided evidence."
    pd_summary = pkpd.get("pd_summary") or "No PD mechanism was identified in the provided evidence."
    caveats = context.get("caveats") or ["No additional caveats supplied."]
    sources = _format_sources(context)

    text = (
        f"## Bottom Line\n"
        f"Bottom-line risk: evidence-grounded prototype assessment for {drug_a} + {drug_b}; "
        f"use the structured risk panel for severity.\n\n"
        f"## Interaction Mechanism\n"
        f"PK: {pk_summary}\n\nPD: {pd_summary}\n\n"
        f"## Clinical Concern\n"
        f"Review the returned risk domains, FAERS terms, and mechanism cards before interpreting clinical importance.\n\n"
        f"## Monitoring & Actions\n"
        f"Qualitative review and monitoring; no patient-specific dosing advice generated by the mock provider.\n\n"
        f"## Evidence Limitations\n"
        f"Evidence used: {sources}\n\n"
        f"Uncertainty / missing evidence: " + "; ".join(str(c) for c in caveats[:5]) + "\n\n"
        f"Research prototype only."
    )
    return {
        "text": _append_disclaimer(text, mode),
        "usage": {},
        "meta": {
            "provider": "mock",
            "model": selected_model,
            "temperature": temperature,
            "seed": seed,
            "ts": int(time.time()),
        },
    }


def _mock_followup_response(
    context: Dict[str, Any],
    mode: str,
    *,
    question: str,
    selected_model: str,
    temperature: float,
    seed: Optional[int],
) -> Dict[str, Any]:
    drugs = context.get("drugs", {}) or {}
    drug_a = (drugs.get("a", {}) or {}).get("name", "Drug A")
    drug_b = (drugs.get("b", {}) or {}).get("name", "Drug B")
    text = (
        "## Direct Answer\n"
        f"For {drug_a} + {drug_b}, this follow-up should be answered against the same evidence record: {question}\n\n"
        "## What To Monitor\n"
        "- Use qualitative, evidence-linked monitoring only.\n"
        "- Do not add numeric dosing or timing details unless they are present in the retrieved context.\n\n"
        "## Evidence Boundary\n"
        "This mock follow-up does not call an LLM; inspect the evidence panel for source-level details."
    )
    return {
        "text": _append_disclaimer(text, mode),
        "usage": {},
        "meta": {
            "provider": "mock",
            "model": selected_model,
            "temperature": temperature,
            "seed": seed,
            "followup": True,
            "ts": int(time.time()),
        },
    }


def _generate_nvidia_response(
    prompt: str,
    mode: str,
    *,
    model_name: str,
    temperature: float,
    top_p: float,
    max_tokens: Optional[int],
    stream: bool,
    seed: Optional[int],
) -> Dict[str, Any]:
    settings = get_settings()
    if not settings.nvidia_api_key:
        return _fallback("NVIDIA provider selected but NVIDIA_API_KEY is not configured.")
    if not model_name:
        return _fallback("NVIDIA provider selected but NVIDIA_MODEL is not configured.")

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(temperature),
        "top_p": float(top_p),
        "stream": bool(stream),
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if seed is not None:
        payload["seed"] = int(seed)
    if settings.nvidia_reasoning_effort in {"low", "medium", "high"}:
        payload["reasoning_effort"] = settings.nvidia_reasoning_effort

    import requests
    from requests import exceptions as request_exceptions

    start = time.time()
    endpoint_url = _nvidia_chat_completions_url(settings.nvidia_base_url)
    attempts = _nvidia_retry_attempts()
    last_error = ""

    for attempt in range(attempts):
        try:
            response = requests.post(
                endpoint_url,
                headers={
                    "Authorization": f"Bearer {settings.nvidia_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream" if stream else "application/json",
                },
                json=payload,
                stream=bool(stream),
                timeout=settings.llm_timeout_s,
            )
            if response.status_code != 200:
                last_error = _format_nvidia_error(response, endpoint_url)
                if _should_retry_nvidia_response(response.status_code, attempt, attempts):
                    _close_response(response)
                    _sleep_before_nvidia_retry(attempt)
                    continue
                return _fallback(last_error)

            usage = {}
            if stream:
                text = _collect_openai_stream(response)
            else:
                data = response.json()
                usage = data.get("usage", {})
                choices = data.get("choices") or []
                message = (choices[0].get("message") or {}) if choices else {}
                text = _extract_openai_message_text(message)
                if not text:
                    text = _extract_openai_reasoning_text(message)
            if not text:
                last_error = (
                    "NVIDIA provider returned an empty visible answer. For GPT-OSS models, increase "
                    "LLM_MAX_TOKENS or set NVIDIA_REASONING_EFFORT=low in .env."
                )
                if attempt < attempts - 1:
                    _sleep_before_nvidia_retry(attempt)
                    continue
                return _fallback(last_error)
            return {
                "text": _append_disclaimer(text, mode),
                "usage": usage,
                "meta": {
                    "provider": "nvidia",
                    "model": model_name,
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_tokens": max_tokens,
                    "stream": bool(stream),
                    "seed": seed,
                    "latency_seconds": round(time.time() - start, 3),
                    "retry_attempts": attempt + 1,
                    "ts": int(time.time()),
                },
            }
        except request_exceptions.Timeout:
            last_error = (
                "NVIDIA provider timed out before returning a completion. The endpoint and key may be configured, "
                "but the hosted model did not respond within the configured timeout. Try NVIDIA_REASONING_EFFORT=low, "
                "a smaller LLM_MAX_TOKENS value, or retry after checking NVIDIA service status."
            )
            if attempt < attempts - 1:
                _sleep_before_nvidia_retry(attempt)
                continue
            return _fallback(last_error)
        except request_exceptions.RequestException as exc:
            last_error = f"NVIDIA provider request exception: {exc}"
            if attempt < attempts - 1:
                _sleep_before_nvidia_retry(attempt)
                continue
            return _fallback(last_error)
        except ValueError as exc:
            last_error = f"NVIDIA provider returned an invalid JSON response: {exc}"
            if attempt < attempts - 1:
                _sleep_before_nvidia_retry(attempt)
                continue
            return _fallback(last_error)

    return _fallback(last_error or "NVIDIA provider failed after retries.")


def _nvidia_retry_attempts() -> int:
    raw = os.getenv("NVIDIA_RETRY_ATTEMPTS", "2")
    try:
        return max(1, min(int(raw), 5))
    except ValueError:
        return 2


def _should_retry_nvidia_response(status_code: int, attempt: int, attempts: int) -> bool:
    return status_code in {502, 503, 504} and attempt < attempts - 1


def _sleep_before_nvidia_retry(attempt: int) -> None:
    time.sleep(min(2.0 * (attempt + 1), 6.0))


def _close_response(response: Any) -> None:
    try:
        response.close()
    except Exception:
        pass


def _nvidia_chat_completions_url(base_url: str) -> str:
    """Accept either a root URL, /v1 base URL, or full chat-completions endpoint."""
    default_base = "https://integrate.api.nvidia.com/v1"
    cleaned = (base_url or default_base).strip().rstrip("/")
    if not cleaned:
        cleaned = default_base

    if cleaned.endswith("/v1/chat/completions") or cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return cleaned + "/chat/completions"
    return cleaned + "/v1/chat/completions"


def _format_nvidia_error(response: Any, endpoint_url: str) -> str:
    preview = _response_error_preview(response)
    safe_url = _redact_url(endpoint_url)
    msg = f"NVIDIA provider error {response.status_code} at {safe_url}: {preview}"
    if response.status_code == 404:
        msg += (
            ". The client normalized the endpoint to NVIDIA's documented chat-completions route. "
            "If this repeats after restarting Streamlit, verify model access for this API key and "
            "run scripts/check_nvidia.py --stream to test NVIDIA outside the frontend."
        )
    return msg


def _response_error_preview(response: Any, max_chars: int = 240) -> str:
    try:
        data = response.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            text = str(err.get("message") or err)
        else:
            text = str(data)
    except Exception:
        text = str(getattr(response, "text", "") or "")
    text = re.sub(r"\s+", " ", text).strip() or "empty response body"
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _extract_openai_message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return ""


def _extract_openai_reasoning_text(message: Dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return "(invalid endpoint URL)"


def _collect_openai_stream(response: Any) -> str:
    content_chunks: List[str] = []
    reasoning_chunks: List[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = str(raw_line).strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in data.get("choices", []) or []:
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content")
            if reasoning:
                reasoning_chunks.append(str(reasoning))
            content = delta.get("content")
            if content:
                content_chunks.append(str(content))
    content = "".join(content_chunks).strip()
    if content:
        return content
    return "".join(reasoning_chunks).strip()


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
        medication_set = (context or {}).get("medication_set") or {}
        medset_drugs = medication_set.get("drugs") or []
        is_medication_set = len(medset_drugs) > 2
        if mode_lower in {"patient", "pt"}:
            if is_medication_set:
                blocks["USER_QUESTION"] = f"Can I take this medication set together: {', '.join(medset_drugs)}?"
            else:
                blocks["USER_QUESTION"] = f"Can I take {blocks.get('DRUG_A', 'drug A')} and {blocks.get('DRUG_B', 'drug B')} together?"
        elif (
            mode_lower in {"pharma", "pv", "safety", "pharmacovigilance", "pharmaceuticals", "research"}
            or "pharmacovigilance" in mode_lower
            or "research" in mode_lower
        ):
            if is_medication_set:
                blocks["USER_QUESTION"] = f"Prepare a medication-set risk brief for: {', '.join(medset_drugs)}."
            else:
                blocks["USER_QUESTION"] = f"Prepare a risk brief for {blocks.get('DRUG_A', 'drug A')} + {blocks.get('DRUG_B', 'drug B')}."
        else:
            if is_medication_set:
                blocks["USER_QUESTION"] = f"Evaluate potential interactions across this medication set: {', '.join(medset_drugs)}."
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


def build_followup_prompt(
    context: Dict[str, Any],
    mode: str,
    *,
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    prior_assessment: Optional[str] = None,
) -> str:
    """Build a short follow-up-only prompt without reusing the full analysis template."""
    blocks = _summarize_context(context or {}, mode)
    hist_block, hist_truncated = _format_history(history or [], budget_chars=900)
    prior = _compact_text(prior_assessment or "", max_chars=1800)
    raw_context = _compact_json(context or {}, max_chars=2200)
    question_text = _compact_text(question or "", max_chars=1000)

    mode_lower = (mode or "").lower()
    if mode_lower in {"patient", "pt"}:
        audience_instruction = (
            "Audience: patient-facing. Use plain language and do not suggest changing, stopping, "
            "or dose-adjusting medicine without a doctor or pharmacist."
        )
        word_limit = "120-180 words"
    elif (
        mode_lower in {"pharma", "pv", "safety", "pharmacovigilance", "pharmaceuticals", "research", "pv_research"}
        or "pharmacovigilance" in mode_lower
        or "research" in mode_lower
    ):
        audience_instruction = (
            "Audience: pharmacovigilance/research. Be neutral, evidence-scoped, and explicit about signal limitations."
        )
        word_limit = "150-250 words"
    else:
        audience_instruction = (
            "Audience: clinician. Be practical, conservative, and concise."
        )
        word_limit = "150-250 words"

    prompt = f"""
SYSTEM:
You answer follow-up questions for an existing INFERMed drug-interaction record.
The full interaction analysis has already been shown to the user. Do not repeat it.
Use the retrieved CONTEXT as the primary evidence. You may use general pharmacology knowledge only when you label it exactly as:
"general pharmacology knowledge, not directly observed in the retrieved datasets".

{audience_instruction}

USER FOLLOW-UP QUESTION:
{question_text}

DRUG PAIR:
{blocks.get("DRUG_A", "Drug A")} + {blocks.get("DRUG_B", "Drug B")}

PREVIOUS ANSWER SUMMARY:
{prior or "(not provided)"}

RECENT FOLLOW-UP THREAD:
{hist_block or "(none)"}

CONTEXT:
- PK summary: {blocks.get("PK_SUMMARY", "(no PK evidence)")}
- PD summary: {blocks.get("PD_SUMMARY", "(no PD evidence)")}
- FAERS summary, associative only: {blocks.get("FAERS_SUMMARY", "No FAERS evidence.")}
- Clinical label/reference context: {blocks.get("CLINICAL_REFERENCE", "(no label/reference evidence)")}
- Research/API enrichment context: {blocks.get("RESEARCH_ENRICHMENT", "(no research enrichment evidence)")}
- Risk flags: {blocks.get("RISK_FLAGS", "(no risk flags)")}
- Mechanistic evidence: {blocks.get("EVIDENCE_TABLE", "(no evidence table)")}
- Sources: {blocks.get("SOURCES", "(no sources listed)")}
- Caveats: {blocks.get("CAVEATS", "(none)")}
- Raw context, compact: {raw_context}

MANDATORY RESPONSE RULES:
1. Answer only the follow-up question. Do not produce the full five-section interaction assessment.
2. Do not use these headings: Bottom Line, Interaction Mechanism, Clinical Concern, Monitoring & Actions, Evidence Limitations.
3. Use exactly these headings:
   ## Direct Answer
   ## What To Monitor
   ## Evidence Boundary
4. Keep the whole answer {word_limit}.
5. Do not invent numeric dose reductions, monitoring intervals, laboratory thresholds, eGFR cutoffs, INR targets, QTc cutoffs, or named alternative drugs unless the exact value/name appears in CONTEXT.
6. If the follow-up asks about patient factors such as elderly age, renal impairment, hepatic impairment, or QT risk, separate what is in the retrieved evidence from general pharmacology knowledge.
7. If evidence is missing, say so directly instead of filling the gap with confident instructions.
8. End with one short sentence reminding that the response is informational and should be checked by a licensed clinician.
"""

    if blocks.get("__TRUNCATED__") or hist_truncated:
        prompt += "\n\n[Note: some context or thread history was truncated for length.]"

    return prompt.strip()

# ========== Template helpers ==========
def _select_template(mode: str) -> str:
    key_raw = (mode or "").strip().lower()
    if key_raw in {"doc", "doctor", "physician", "clinician"}:
        key = "DOCTOR"
    elif key_raw in {"patient", "pt"}:
        key = "PATIENT"
    elif (
        key_raw in {"pharma", "pv", "safety", "pharmacovigilance", "pharmaceuticals", "research"}
        or "pharmacovigilance" in key_raw
        or "research" in key_raw
    ):
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
    if isinstance(xs, dict):
        xs = [xs]
    out: List[str] = []
    for x in xs or []:
        if isinstance(x, dict):
            s = str(
                x.get("label")
                or x.get("name")
                or x.get("displayName")
                or x.get("uniprot_id")
                or x.get("pathway_name")
                or x.get("enzyme_name")
                or x.get("uri")
                or ""
            ).strip()
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


def _compact_text(value: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text

# ========== Context summarization & truncation ==========
def _summarize_context(ctx: Dict[str, Any], mode: str) -> Dict[str, str]:
    drugs = ctx.get("drugs", {}) or {}
    medication_set = ctx.get("medication_set") or {}
    medset_drugs = medication_set.get("drugs") or []
    if len(medset_drugs) > 2:
        drug_a = "Medication set"
        drug_b = ", ".join(str(item) for item in medset_drugs)
    else:
        drug_a = (drugs.get("a", {}) or {}).get("name", "Drug A")
        drug_b = (drugs.get("b", {}) or {}).get("name", "Drug B")

    medication_set_txt = _format_medication_set(ctx)
    pk_txt, pk_len, pk_prio = _format_pk(ctx)
    pk_meta_txt = _format_pk_meta(ctx)  # NEW: PK metadata from PubChem
    pd_txt, pd_len, pd_prio = _format_pd(ctx)
    faers_txt, faers_len = _format_faers(ctx, limit=5)
    clinical_reference_txt = _format_clinical_reference(ctx)
    research_enrichment_txt = _format_research_enrichment(ctx)
    flags_txt = _format_flags(ctx)
    table_txt = _format_evidence_table(ctx, mode)
    sources_txt = _format_sources(ctx)
    caveats_txt = _format_caveats(ctx)

    if (mode or "").lower() in {"doctor", "physician", "pharma", "pv", "safety"}:
        budget = 2400
    else:
        budget = 1500

    parts: List[Tuple[str, int, int, str]] = [
        ("MedicationSet", len(medication_set_txt), 1, medication_set_txt),
        ("PK", pk_len, pk_prio, pk_txt),
        ("PK_META", len(pk_meta_txt), 2, pk_meta_txt),  # NEW: PK metadata (high priority, after PK)
        ("PD", pd_len, pd_prio, pd_txt),
        ("FAERS", faers_len, 5, faers_txt),
        ("ClinicalReference", len(clinical_reference_txt), 4, clinical_reference_txt),
        ("ResearchEnrichment", len(research_enrichment_txt), 6, research_enrichment_txt),
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
        "MEDICATION_SET_SUMMARY": g("MedicationSet", ""),
        "PK_SUMMARY": g("PK", "(no PK evidence)"),
        "PK_META": g("PK_META", ""),  # NEW: PK metadata from PubChem (empty if not available)
        "PD_SUMMARY": g("PD", "(no PD evidence)"),
        "FAERS_SUMMARY": g("FAERS", "No evidence from FAERS."),
        "CLINICAL_REFERENCE": g("ClinicalReference", "(no label/reference evidence)"),
        "RESEARCH_ENRICHMENT": g("ResearchEnrichment", "(no research enrichment evidence)"),
        "RISK_FLAGS": g("Flags", "(no tabular risk flags)"),
        "EVIDENCE_TABLE": g("Table", "(no evidence table)"),
        "SOURCES": g("Sources", "(no sources listed)"),
        "CAVEATS": g("Caveats", "(none)"),
        "__TRUNCATED__": "1" if (truncated_by_budget or input_overflow) else "",
    }

# ========== Formatting helpers ==========
def _format_medication_set(ctx: Dict[str, Any]) -> str:
    medication_set = ctx.get("medication_set") or {}
    if not isinstance(medication_set, dict):
        return ""
    drugs = medication_set.get("drugs") or []
    pairs = medication_set.get("top_pairs") or medication_set.get("evaluated_pairs") or []
    if not drugs and not pairs:
        return ""

    parts: List[str] = []
    if drugs:
        parts.append(f"Medication set: {', '.join(str(item) for item in drugs)}")
    if pairs:
        pair_bits = []
        for row in pairs[:8]:
            if not isinstance(row, dict):
                continue
            pair = row.get("pair") or []
            pair_label = " + ".join(str(item) for item in pair)
            risk = row.get("risk_level") or "unknown"
            confidence = row.get("confidence") or "low"
            pk = _compact_text(str(row.get("pk_summary") or ""), 140)
            pd = _compact_text(str(row.get("pd_summary") or ""), 120)
            pair_bits.append(f"{pair_label}: risk={risk}, confidence={confidence}, PK={pk or 'none'}, PD={pd or 'none'}")
        if pair_bits:
            parts.append("Pair ranking: " + " || ".join(pair_bits))
    patient_context = medication_set.get("patient_context") or {}
    if isinstance(patient_context, dict) and patient_context:
        parts.append("Patient context supplied: " + _compact_text(json.dumps(patient_context, ensure_ascii=False, default=str), 500))
    return " || ".join(parts)


def _format_clinical_reference(ctx: Dict[str, Any]) -> str:
    clinical = ((ctx.get("signals") or {}).get("clinical_reference") or {})
    if not isinstance(clinical, dict):
        return ""

    parts: List[str] = []

    rxnorm = clinical.get("rxnorm") or {}
    for side, label in (("a", "A"), ("b", "B")):
        payload = rxnorm.get(side) or {}
        if not isinstance(payload, dict) or not payload.get("resolved"):
            continue
        cls = payload.get("classes") or []
        class_names = []
        for row in cls[:4]:
            if isinstance(row, dict) and row.get("class_name"):
                class_names.append(str(row["class_name"]))
        detail = f"{label} RxCUI={payload.get('rxcui')} name={payload.get('name')}"
        if class_names:
            detail += " classes=" + ", ".join(class_names[:4])
        parts.append(detail)

    labels = clinical.get("openfda_label") or {}
    for side, label in (("a", "A"), ("b", "B")):
        payload = labels.get(side) or {}
        if not isinstance(payload, dict) or not payload.get("found"):
            continue
        sections = payload.get("sections") or {}
        section_parts = []
        for key in ("boxed_warning", "contraindications", "drug_interactions", "warnings_and_cautions", "clinical_pharmacology"):
            text = str(sections.get(key) or "").strip()
            if text:
                section_parts.append(f"{key}: {_compact_text(text, 240)}")
        if section_parts:
            parts.append(f"{label} openFDA label ({payload.get('effective_time') or 'date unknown'}): " + " | ".join(section_parts[:3]))

    dailymed = clinical.get("dailymed") or {}
    for side, label in (("a", "A"), ("b", "B")):
        payload = dailymed.get(side) or {}
        records = payload.get("records") if isinstance(payload, dict) else None
        if not records:
            continue
        first = records[0]
        if isinstance(first, dict):
            title = first.get("title") or first.get("set_id")
            if title:
                parts.append(f"{label} DailyMed SPL record: {title}")

    fda_ddi = clinical.get("fda_ddi_reference") or {}
    for side, label in (("a", "A"), ("b", "B")):
        payload = fda_ddi.get(side) or {}
        matches = payload.get("matches") if isinstance(payload, dict) else None
        if not matches:
            continue
        row_summaries = []
        for match in matches[:3]:
            if not isinstance(match, dict):
                continue
            row = match.get("row") or {}
            row_summaries.append("; ".join(f"{k}={v}" for k, v in list(row.items())[:3] if v))
        if row_summaries:
            parts.append(f"{label} FDA CYP/transporter table match: " + " / ".join(row_summaries))

    return " || ".join(parts[:8])


def _format_research_enrichment(ctx: Dict[str, Any]) -> str:
    enrichment = ((ctx.get("signals") or {}).get("research_enrichment") or {})
    if not isinstance(enrichment, dict):
        return ""

    parts: List[str] = []

    literature = enrichment.get("europe_pmc") or {}
    if isinstance(literature, dict) and literature.get("articles"):
        article_bits = []
        for row in (literature.get("articles") or [])[:3]:
            if not isinstance(row, dict):
                continue
            title = _compact_text(row.get("title") or "", 120)
            year = str(row.get("year") or "").strip()
            if title:
                article_bits.append(f"{title}" + (f" ({year})" if year else ""))
        if article_bits:
            parts.append("Europe PMC literature metadata: " + " | ".join(article_bits))

    pgx = enrichment.get("fda_pgx") or {}
    if isinstance(pgx, dict) and pgx.get("found"):
        snippets = []
        for side in ("a", "b"):
            for row in (pgx.get(side) or [])[:2]:
                if isinstance(row, dict) and row.get("snippet"):
                    snippets.append(_compact_text(str(row["snippet"]), 160))
        if snippets:
            parts.append("FDA PGx page matches: " + " | ".join(snippets[:3]))

    stringdb = enrichment.get("stringdb") or {}
    if isinstance(stringdb, dict) and stringdb.get("found"):
        mapped = []
        for row in (stringdb.get("mapped") or [])[:5]:
            if isinstance(row, dict):
                mapped.append(str(row.get("preferred_name") or row.get("query") or "").strip())
        interactions = []
        for row in (stringdb.get("interactions") or [])[:3]:
            if isinstance(row, dict):
                a = row.get("protein_a")
                b = row.get("protein_b")
                if a and b:
                    interactions.append(f"{a}-{b} score={row.get('score')}")
        detail = []
        if mapped:
            detail.append("mapped proteins=" + ", ".join([m for m in mapped if m][:5]))
        if interactions:
            detail.append("protein associations=" + "; ".join(interactions))
        if detail:
            parts.append("STRING hypothesis context: " + " | ".join(detail))

    drugcentral = enrichment.get("drugcentral") or {}
    if isinstance(drugcentral, dict):
        drugcentral_bits = []
        for side in ("a", "b"):
            payload = drugcentral.get(side) or {}
            if not isinstance(payload, dict) or not payload.get("found"):
                continue
            structure = payload.get("structure") or {}
            label = structure.get("name") or payload.get("drug") or side
            targets = []
            for row in (payload.get("targets") or [])[:4]:
                if not isinstance(row, dict):
                    continue
                gene = row.get("gene")
                action = row.get("action_type") or row.get("act_type")
                if gene:
                    targets.append(str(gene) + (f" ({action})" if action else ""))
            if targets:
                drugcentral_bits.append(f"{label}: " + ", ".join(targets))
        if drugcentral_bits:
            parts.append("DrugCentral target/activity context: " + " | ".join(drugcentral_bits))

    open_targets = enrichment.get("open_targets") or {}
    if isinstance(open_targets, dict):
        hits = []
        for side in ("a", "b"):
            payload = open_targets.get(side) or {}
            if not isinstance(payload, dict):
                continue
            for row in (payload.get("hits") or [])[:3]:
                if isinstance(row, dict):
                    name = row.get("name") or row.get("id")
                    entity = row.get("entity")
                    if name:
                        hits.append(f"{name}" + (f" ({entity})" if entity else ""))
        if hits:
            parts.append("Open Targets search hits: " + ", ".join(hits[:6]))

    biogrid = enrichment.get("biogrid") or {}
    if isinstance(biogrid, dict):
        if biogrid.get("interactions"):
            rows = []
            for row in (biogrid.get("interactions") or [])[:3]:
                if isinstance(row, dict):
                    a = row.get("interactor_a")
                    b = row.get("interactor_b")
                    system = row.get("experimental_system")
                    if a and b:
                        rows.append(f"{a}-{b}" + (f" ({system})" if system else ""))
            if rows:
                parts.append("BioGRID interaction context: " + "; ".join(rows))
        elif biogrid.get("reason"):
            parts.append("BioGRID unavailable: " + str(biogrid.get("reason")))

    return " || ".join(parts[:8])


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
    nci_rows = tab.get("nci_almanac") or []
    if isinstance(nci_rows, list) and nci_rows:
        top_score = nci_rows[0].get("score") if isinstance(nci_rows[0], dict) else None
        flags.append(f"NCI-ALMANAC(rows)={len(nci_rows)}")
        if top_score is not None:
            flags.append(f"NCI-ALMANAC(top_score)={top_score}")
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

    chembl_summary = _format_chembl_enrichment(mech.get("chembl_enrichment"))
    enrichment_sources = []
    if mech.get("uniprot_ids_a") or mech.get("uniprot_ids_b") or mech.get("uniprot_targets_a") or mech.get("uniprot_targets_b"):
        enrichment_sources.append("UniProt")
    if mech.get("kegg_pathways_a") or mech.get("kegg_pathways_b") or mech.get("kegg_common_pathways") or mech.get("kegg_enzymes_a") or mech.get("kegg_enzymes_b"):
        enrichment_sources.append("KEGG")
    if mech.get("reactome_pathways_a") or mech.get("reactome_pathways_b"):
        enrichment_sources.append("Reactome")
    if chembl_summary:
        enrichment_sources.append("ChEMBL")

    if enrichment_sources:
        lines.append("Returned enrichment sources: " + ", ".join(enrichment_sources))

    uni_a = _as_str_list(mech.get("uniprot_ids_a", []))[:8]
    uni_b = _as_str_list(mech.get("uniprot_ids_b", []))[:8]
    if uni_a:
        lines.append(f"UniProt A accessions: {', '.join(uni_a)}")
    if uni_b:
        lines.append(f"UniProt B accessions: {', '.join(uni_b)}")

    uni_targets_a = _format_protein_records(mech.get("uniprot_targets_a"))[:5]
    uni_targets_b = _format_protein_records(mech.get("uniprot_targets_b"))[:5]
    if uni_targets_a:
        lines.append(f"UniProt A target details: {', '.join(uni_targets_a)}")
    if uni_targets_b:
        lines.append(f"UniProt B target details: {', '.join(uni_targets_b)}")

    kegg_common = _format_pathway_records(mech.get("kegg_common_pathways"))[:5]
    kegg_a = _format_pathway_records(mech.get("kegg_pathways_a"))[:5]
    kegg_b = _format_pathway_records(mech.get("kegg_pathways_b"))[:5]
    kegg_enzymes_a = _format_enzyme_records(mech.get("kegg_enzymes_a"))[:5]
    kegg_enzymes_b = _format_enzyme_records(mech.get("kegg_enzymes_b"))[:5]
    reactome_a = _format_pathway_records(mech.get("reactome_pathways_a"))[:5]
    reactome_b = _format_pathway_records(mech.get("reactome_pathways_b"))[:5]
    if kegg_common:
        lines.append(f"KEGG common pathways: {', '.join(kegg_common)}")
    if kegg_a:
        lines.append(f"KEGG A pathways: {', '.join(kegg_a)}")
    if kegg_b:
        lines.append(f"KEGG B pathways: {', '.join(kegg_b)}")
    if kegg_enzymes_a:
        lines.append(f"KEGG A enzyme hints: {', '.join(kegg_enzymes_a)}")
    if kegg_enzymes_b:
        lines.append(f"KEGG B enzyme hints: {', '.join(kegg_enzymes_b)}")
    if reactome_a:
        lines.append(f"Reactome A pathways: {', '.join(reactome_a)}")
    if reactome_b:
        lines.append(f"Reactome B pathways: {', '.join(reactome_b)}")

    if chembl_summary:
        lines.append(f"ChEMBL activity: {chembl_summary}")

    nci_summary = _format_nci_almanac(ctx)
    if nci_summary:
        lines.append(nci_summary)
    
    return " | ".join(lines)


def _format_nci_almanac(ctx: Dict[str, Any]) -> str:
    tab = (ctx.get("signals", {}) or {}).get("tabular", {}) or {}
    rows = tab.get("nci_almanac") or []
    if not isinstance(rows, list) or not rows:
        return ""

    parts: List[str] = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        score = row.get("score")
        panel = row.get("panel") or "unknown panel"
        cell = row.get("cell_name") or "unknown cell"
        expected = row.get("expected_growth")
        observed = row.get("percent_growth")
        text = f"{panel}/{cell}: score={score}"
        if expected is not None and observed is not None:
            text += f" (expected_growth={expected}, observed_growth={observed})"
        parts.append(text)
    if not parts:
        return ""
    return (
        "NCI-ALMANAC experimental combo screen: "
        + "; ".join(parts)
        + ". Score means expected growth minus observed growth; use as oncology cell-line hypothesis evidence, not clinical DDI causality."
    )


def _format_pathway_records(value: Any) -> List[str]:
    rows = value or []
    if isinstance(rows, dict):
        rows = [rows]
    out: List[str] = []
    for item in rows:
        if isinstance(item, dict):
            pathway_id = item.get("pathway_id") or item.get("id") or item.get("stId") or item.get("dbId")
            name = item.get("pathway_name") or item.get("name") or item.get("displayName")
            if pathway_id and name:
                out.append(f"{pathway_id}: {name}")
            elif name:
                out.append(str(name))
            elif pathway_id:
                out.append(str(pathway_id))
        else:
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def _format_protein_records(value: Any) -> List[str]:
    rows = value or []
    if isinstance(rows, dict):
        rows = [rows]
    out: List[str] = []
    for item in rows:
        if isinstance(item, dict):
            accession = item.get("uniprot_id") or item.get("primaryAccession") or item.get("accession")
            name = item.get("name") or item.get("protein_name") or item.get("original_id")
            genes = item.get("gene_names") or []
            if isinstance(genes, str):
                genes = [genes]
            gene_text = f" ({', '.join(str(g) for g in genes[:3] if g)})" if genes else ""
            if accession and name:
                out.append(f"{accession}: {name}{gene_text}")
            elif accession:
                out.append(str(accession))
            elif name:
                out.append(str(name))
        else:
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def _format_enzyme_records(value: Any) -> List[str]:
    rows = value or []
    if isinstance(rows, dict):
        rows = [rows]
    out: List[str] = []
    for item in rows:
        if isinstance(item, dict):
            enzyme_id = item.get("enzyme_id") or item.get("id")
            name = item.get("enzyme_name") or item.get("name")
            if enzyme_id and name:
                out.append(f"{enzyme_id}: {name}")
            elif name:
                out.append(str(name))
            elif enzyme_id:
                out.append(str(enzyme_id))
        else:
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def _format_chembl_enrichment(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts: List[str] = []
    for side, label in (("a", "A"), ("b", "B")):
        data = value.get(side)
        if not isinstance(data, dict):
            continue
        validation = data.get("chembl_validation") or {}
        strengths = data.get("enzyme_strength") or {}
        side_parts: List[str] = []
        if validation.get("found"):
            matches = _as_str_list(validation.get("matches", []))[:5]
            mismatches = _as_str_list(validation.get("mismatches", []))[:5]
            if matches:
                side_parts.append("validated " + ", ".join(matches))
            if mismatches:
                side_parts.append("additional " + ", ".join(mismatches))
        for strength in ("strong", "moderate", "weak"):
            values = _as_str_list(strengths.get(strength, []))[:5]
            if values:
                side_parts.append(f"{strength} {', '.join(values)}")
        if side_parts:
            parts.append(f"{label}: " + "; ".join(side_parts))
    return " / ".join(parts)


def _format_sources(ctx: Dict[str, Any]) -> str:
    src = ctx.get("sources", {}) or {}

    def fmt(key: str) -> str:
        lst = src.get(key, []) or []
        return f"{key}: " + (", ".join(lst) if lst else "(none)")

    ordered = [
        "duckdb",
        "qlever",
        "openfda",
        "apis",
        "canonical",
        "semantic",
        "reranking",
        "query_expansion",
        "hybrid_search",
        "adaptive_retrieval",
    ]
    extras = [key for key in src.keys() if key not in ordered]
    return "; ".join(fmt(key) for key in ordered + extras)

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
