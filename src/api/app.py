from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.api.schemas import AnalyzeRequest, FollowUpRequest, MedicationSetAnalyzeRequest
from src.api.transformers import build_interaction_result, cited_cards_from_context
from src.application.commands import AnalyzeMedicationSetCommand
from src.application.use_cases.analyze_medication_set import AnalyzeMedicationSetUseCase
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
    try:
        analysis = AnalyzeMedicationSetUseCase(rag_runner=run_rag).execute(
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
        analysis = AnalyzeMedicationSetUseCase(rag_runner=run_rag).execute(
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

    drug_a, drug_b = analysis.executed_pair
    legacy = build_interaction_result(analysis.rag_output, fallback_drug_a=drug_a, fallback_drug_b=drug_b)
    return {
        **analysis.to_read_model(),
        "top_risks": [analysis.decision.to_dict()],
        "topRisks": [analysis.decision.to_dict()],
        "explanation": {"sections": legacy["assessment"]},
        "evidence_panels": legacy["evidence"],
        "evidencePanels": legacy["evidence"],
        "source_status": legacy["evidence"].get("sources", []),
        "sourceStatus": legacy["evidence"].get("sources", []),
        "limitations": analysis.decision.source_limitations,
        "compatibility": legacy,
    }


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
