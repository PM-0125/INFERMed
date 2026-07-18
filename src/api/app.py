from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.api.schemas import AnalyzeRequest, FollowUpRequest, MedicationSetAnalyzeRequest
from src.api.transformers import build_interaction_result, cited_cards_from_context
from src.application.commands import AnalyzeMedicationSetCommand
from src.application.use_cases.analyze_medication_set import AnalyzeMedicationSetUseCase
from src.application.use_cases.answer_followup import AnswerFollowUpCommand, AnswerFollowUpUseCase
from src.config.settings import get_settings
from src.config.data_policy import get_source_status
from src.llm.llm_interface import generate_followup_response, generate_response
from src.llm.rag_pipeline import get_context_cached, retrieve_and_normalize, run_rag
from src.infrastructure.persistence.event_store import SQLiteEventStore

log = logging.getLogger("uvicorn.error")


def _allowed_origins() -> list[str]:
    raw = os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


app = FastAPI(title="INFERMed API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "ok": True,
        "provider": settings.llm_provider,
        "modelConfigured": bool(settings.nvidia_model if settings.llm_provider == "nvidia" else True),
        "dataMode": settings.data_mode,
    }


@app.post("/api/interactions/analyze")
def analyze_interaction(request: AnalyzeRequest) -> dict[str, Any]:
    try:
        analysis = _execute_analysis(
            AnalyzeMedicationSetCommand(
                medications=request.drugs,
                audience=request.mode,
                refresh_evidence=request.refreshEvidence,
                analysis_depth="standard",
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Interaction analysis failed: {exc}") from exc

    drug_a, drug_b = analysis.executed_pair
    response = build_interaction_result(analysis.rag_output, fallback_drug_a=drug_a, fallback_drug_b=drug_b)
    response.update(analysis.to_read_model())
    return response


@app.post("/api/medication-sets/analyze")
def analyze_medication_set(request: MedicationSetAnalyzeRequest) -> dict[str, Any]:
    try:
        analysis = _execute_analysis(
            AnalyzeMedicationSetCommand(
                medications=request.medication_texts,
                audience=request.audience,
                patient_context=request.patient_context,
                refresh_evidence=request.refresh_evidence,
                analysis_depth=request.analysis_depth,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Medication-set analysis failed: {exc}") from exc

    return _medication_set_response(analysis)


@app.post("/api/medication-sets/analyze/stream")
def analyze_medication_set_stream(request: MedicationSetAnalyzeRequest) -> StreamingResponse:
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def progress(stage: str, message: str, payload: dict[str, Any]) -> None:
        events.put({"type": "progress", "stage": stage, "message": message, "payload": payload})

    def worker() -> None:
        try:
            log.info(
                "INFERMed analysis started: drugs=%s audience=%s refresh=%s",
                request.medication_texts,
                request.audience,
                request.refresh_evidence,
            )
            analysis = _execute_analysis(
                AnalyzeMedicationSetCommand(
                    medications=request.medication_texts,
                    audience=request.audience,
                    patient_context=request.patient_context,
                    refresh_evidence=request.refresh_evidence,
                    analysis_depth=request.analysis_depth,
                ),
                progress_callback=progress,
            )
            log.info("INFERMed analysis completed: analysis_id=%s", analysis.analysis_id)
            events.put({"type": "result", "result": _medication_set_response(analysis)})
        except Exception as exc:
            log.exception("INFERMed analysis failed")
            events.put({"type": "error", "detail": f"Medication-set analysis failed: {exc}"})
        finally:
            events.put(None)

    def stream() -> Any:
        yield _sse({"type": "progress", "stage": "queued", "message": "Analysis queued.", "payload": {}})
        thread = threading.Thread(target=worker, name="infermed-analysis-stream", daemon=True)
        thread.start()
        started = time.monotonic()
        heartbeat = 0
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                if not thread.is_alive():
                    continue
                heartbeat += 1
                elapsed = int(time.monotonic() - started)
                log.info(
                    "INFERMed analysis heartbeat: final LLM call still running; elapsed_s=%s heartbeat=%s",
                    elapsed,
                    heartbeat,
                )
                continue
            if item is None:
                break
            yield _sse(item)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _medication_set_response(analysis: Any) -> dict[str, Any]:
    drug_a, drug_b = analysis.executed_pair
    legacy = build_interaction_result(analysis.rag_output, fallback_drug_a=drug_a, fallback_drug_b=drug_b)
    return {
        **legacy,
        **analysis.to_read_model(),
        "top_risks": [
            item.decision.to_dict()
            for item in analysis.pair_results
            if item.decision is not None
        ][:8] or [analysis.decision.to_dict()],
        "topRisks": [
            item.decision.to_dict()
            for item in analysis.pair_results
            if item.decision is not None
        ][:8] or [analysis.decision.to_dict()],
        "explanation": {"sections": legacy["assessment"]},
        "evidence_panels": legacy["evidence"],
        "evidencePanels": legacy["evidence"],
        "source_status": legacy["evidence"].get("sources", []),
        "sourceStatus": legacy["evidence"].get("sources", []),
        "limitations": analysis.decision.source_limitations,
        "compatibility": legacy,
    }


@app.on_event("startup")
def startup_readiness_log() -> None:
    report = _readiness_report(check_llm=os.getenv("INFERMED_STARTUP_LLM_CHECK", "").lower() in {"1", "true", "yes", "on"})
    log.info(
        "INFERMed readiness: ready=%s provider=%s model=%s data_mode=%s sources=%s unavailable=%s",
        report["ready"],
        report["llm"]["provider"],
        report["llm"].get("model"),
        report["runtime"]["data_mode"],
        report["sources"]["enabled_count"],
        report["sources"]["unavailable_enabled_count"],
    )
    for issue in report["issues"][:12]:
        log.warning("INFERMed readiness issue: %s", issue)


@app.get("/api/readiness")
def readiness(llm: bool = Query(False, description="Run a tiny real LLM connectivity ping.")) -> dict[str, Any]:
    return _readiness_report(check_llm=llm)


@app.post("/api/interactions/followup")
def interaction_followup(request: FollowUpRequest) -> dict[str, Any]:
    if request.followUpCount >= 3:
        raise HTTPException(
            status_code=400,
            detail="Follow-up limit reached for this interaction. Start a new analysis to continue.",
        )

    try:
        result = AnswerFollowUpUseCase(
            followup_generator=generate_followup_response,
            context_runner=_retrieve_pair_context,
            cited_cards_from_context=cited_cards_from_context,
            snapshot_store=_event_store_or_none(),
        ).execute(
            AnswerFollowUpCommand(
                question=request.question,
                drugs=request.drugs,
                mode=request.mode,
                context_id=request.contextId,
                patient_context=request.patient_context,
                history=[turn.model_dump() for turn in request.history],
                prior_assessment=request.priorAssessment,
                follow_up_count=request.followUpCount,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Follow-up failed: {exc}") from exc

    return result.to_dict()


def _execute_analysis(
    command: AnalyzeMedicationSetCommand,
    *,
    progress_callback: Any | None = None,
) -> Any:
    return AnalyzeMedicationSetUseCase(
        rag_runner=run_rag,
        context_runner=_retrieve_pair_context,
        answer_generator=_generate_final_answer,
        progress_callback=progress_callback,
    ).execute(command)


def _retrieve_pair_context(drug_a: str, drug_b: str, **kwargs: Any) -> dict[str, Any]:
    settings = get_settings()
    force_refresh = bool(kwargs.get("force_refresh"))
    if force_refresh:
        return retrieve_and_normalize(
            drug_a,
            drug_b,
            parquet_dir=settings.duckdb_dir,
            openfda_cache=settings.openfda_cache_dir,
        )
    try:
        context, _cache_key = get_context_cached(
            drug_a,
            drug_b,
            parquet_dir=settings.duckdb_dir,
            openfda_cache=settings.openfda_cache_dir,
        )
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        context, _cache_key = get_context_cached(drug_a, drug_b)
    return context


def _generate_final_answer(context: dict[str, Any], mode: str, **kwargs: Any) -> dict[str, Any]:
    patient_context = kwargs.get("patient_context")
    if patient_context:
        context = dict(context)
        medication_set = dict(context.get("medication_set") or {})
        medication_set["patient_context"] = patient_context
        context["medication_set"] = medication_set
    settings = get_settings()
    log.info(
        "INFERMed final LLM call started: provider=%s model=%s reasoning=%s stream=%s max_tokens=%s timeout_s=%s",
        settings.llm_provider,
        settings.nvidia_model if settings.llm_provider == "nvidia" else settings.ollama_model,
        settings.nvidia_reasoning_effort,
        settings.llm_stream,
        settings.llm_max_tokens,
        settings.llm_timeout_s,
    )
    started = time.monotonic()
    answer = generate_response(context, mode, seed=42, temperature=settings.llm_temperature, model_name=None)
    log.info(
        "INFERMed final LLM call completed: elapsed_s=%.1f answer_chars=%s provider=%s",
        time.monotonic() - started,
        len(str(answer.get("text") or "")),
        (answer.get("meta") or {}).get("provider"),
    )
    return answer


def _event_store_or_none() -> SQLiteEventStore | None:
    settings = get_settings()
    if not settings.enable_event_store:
        return None


def _readiness_report(*, check_llm: bool = False) -> dict[str, Any]:
    settings = get_settings()
    source_rows = get_source_status(settings)
    sources = [
        {
            "name": row.name,
            "enabled": row.enabled,
            "available": row.available,
            "reason": row.reason,
        }
        for row in source_rows
    ]
    issues = [
        f"{row.name}: {row.reason or 'enabled but unavailable'}"
        for row in source_rows
        if row.enabled and not row.available
    ]
    llm_status = _llm_readiness(settings, check_llm=check_llm)
    if not llm_status["configured"] or (check_llm and not llm_status.get("connected", False)):
        issues.append(f"LLM: {llm_status.get('detail')}")
    ready = not issues
    return {
        "ready": ready,
        "runtime": {
            "data_mode": settings.data_mode,
            "cache_backend": settings.cache_backend,
            "duckdb_dir": settings.duckdb_dir,
            "openfda_cache_dir": settings.openfda_cache_dir,
        },
        "llm": llm_status,
        "sources": {
            "enabled_count": sum(1 for row in source_rows if row.enabled),
            "available_enabled_count": sum(1 for row in source_rows if row.enabled and row.available),
            "unavailable_enabled_count": sum(1 for row in source_rows if row.enabled and not row.available),
            "items": sources,
        },
        "issues": issues,
    }


def _llm_readiness(settings: Any, *, check_llm: bool) -> dict[str, Any]:
    if settings.llm_provider == "nvidia":
        status = {
            "provider": "nvidia",
            "model": settings.nvidia_model,
            "configured": bool(settings.nvidia_api_key and settings.nvidia_model),
            "connected": None,
            "checked": False,
            "detail": "NVIDIA_API_KEY and NVIDIA_MODEL configured" if settings.nvidia_api_key and settings.nvidia_model else "Missing NVIDIA_API_KEY or NVIDIA_MODEL",
            "stream": settings.llm_stream,
            "timeout_s": settings.llm_timeout_s,
            "max_tokens": settings.llm_max_tokens,
            "reasoning_effort": settings.nvidia_reasoning_effort,
        }
        if check_llm and status["configured"]:
            status.update(_nvidia_ping(settings))
        return status
    if settings.llm_provider == "ollama":
        return {
            "provider": "ollama",
            "model": settings.ollama_model,
            "configured": True,
            "connected": None,
            "checked": False,
            "detail": settings.ollama_host,
        }
    return {
        "provider": settings.llm_provider,
        "model": "mock",
        "configured": True,
        "connected": True,
        "checked": False,
        "detail": "Mock provider configured",
    }


def _nvidia_ping(settings: Any) -> dict[str, Any]:
    import requests

    started = time.monotonic()
    url = settings.nvidia_base_url.rstrip("/")
    endpoint = url + "/chat/completions" if url.endswith("/v1") else url + "/v1/chat/completions"
    payload = {
        "model": settings.nvidia_model,
        "messages": [{"role": "user", "content": "Reply with OK only."}],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 16,
        "stream": False,
    }
    if settings.nvidia_reasoning_effort in {"low", "medium", "high"}:
        payload["reasoning_effort"] = settings.nvidia_reasoning_effort
    try:
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {settings.nvidia_api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=min(float(settings.llm_timeout_s), 30.0),
        )
    except requests.RequestException as exc:
        return {"checked": True, "connected": False, "detail": f"NVIDIA ping failed: {exc}", "latency_s": round(time.monotonic() - started, 2)}
    if response.status_code != 200:
        return {
            "checked": True,
            "connected": False,
            "detail": f"NVIDIA ping HTTP {response.status_code}: {response.text[:160]}",
            "latency_s": round(time.monotonic() - started, 2),
        }
    return {"checked": True, "connected": True, "detail": "NVIDIA ping succeeded", "latency_s": round(time.monotonic() - started, 2)}
    try:
        return SQLiteEventStore(settings.sqlite_cache_path)
    except Exception:
        return None


def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"
