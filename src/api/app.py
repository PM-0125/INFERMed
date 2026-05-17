from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.api.schemas import AnalyzeRequest, FollowUpRequest
from src.api.transformers import build_interaction_result, cited_cards_from_context
from src.config.settings import get_settings
from src.llm.llm_interface import generate_followup_response, generate_response
from src.llm.rag_pipeline import get_context_cached, run_rag


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
    drug_a, drug_b = request.drugs[0], request.drugs[1]
    try:
        rag_output = run_rag(
            drug_a,
            drug_b,
            mode=request.mode,
            use_cache_context=not request.refreshEvidence,
            use_cache_response=False,
            model_name=None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Interaction analysis failed: {exc}") from exc

    return build_interaction_result(rag_output, fallback_drug_a=drug_a, fallback_drug_b=drug_b)


@app.post("/api/interactions/followup")
def interaction_followup(request: FollowUpRequest) -> dict[str, Any]:
    if request.followUpCount >= 3:
        raise HTTPException(
            status_code=400,
            detail="Follow-up limit reached for this interaction. Start a new analysis to continue.",
        )

    drug_a, drug_b = request.drugs[0], request.drugs[1]
    try:
        context, _cache_key = get_context_cached(drug_a, drug_b)
        answer = generate_followup_response(
            context,
            request.mode,
            question=request.question,
            history=[turn.model_dump() for turn in request.history],
            prior_assessment=request.priorAssessment,
            model_name=None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Follow-up failed: {exc}") from exc

    return {
        "answer": str(answer.get("text") or "").strip() or "No answer was generated.",
        "citedCards": cited_cards_from_context(context),
    }
