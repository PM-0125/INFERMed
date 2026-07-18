from __future__ import annotations

from src.domain.decision.entities import ClinicalAction
from src.domain.patient.entities import PatientContext


def recommended_action_for_risk(risk_level: str) -> ClinicalAction:
    if risk_level in {"contraindicated", "avoid"}:
        return "avoid"
    if risk_level == "major":
        return "specialist_review"
    if risk_level == "moderate":
        return "monitor"
    if risk_level in {"minor", "theoretical"}:
        return "counsel"
    return "insufficient_evidence"


def missing_patient_factors_for_decision(patient_context: PatientContext | None = None) -> list[str]:
    if patient_context is None:
        return ["renal function", "hepatic function", "baseline ECG/QTc", "current INR"]
    labels = {
        "age_years": "age",
        "sex": "sex",
        "renal_function": "renal function",
        "hepatic_function": "hepatic function",
        "qt_risk": "baseline ECG/QTc",
        "current_inr": "current INR",
    }
    return [labels.get(field, field) for field in patient_context.missing_fields]


def patient_amplifiers(patient_context: PatientContext | None = None) -> list[str]:
    if patient_context is None:
        return []
    out: list[str] = []
    if patient_context.age_years is not None and patient_context.age_years >= 75:
        out.append("advanced age")
    if patient_context.renal_function in {"moderate_impairment", "severe_impairment", "dialysis"}:
        out.append("renal impairment")
    if patient_context.hepatic_function in {"moderate_impairment", "severe_impairment"}:
        out.append("hepatic impairment")
    if patient_context.qt_risk in {"known_long_qt", "possible_qt_risk"}:
        out.append("baseline QT risk")
    if patient_context.current_inr is not None:
        out.append("known INR context")
    return out
