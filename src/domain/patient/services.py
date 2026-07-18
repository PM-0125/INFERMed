from __future__ import annotations

from typing import Any

from src.domain.patient.entities import (
    ConditionContext,
    GenotypeContext,
    LabObservation,
    MonitoringState,
    PatientContext,
)

_REQUIRED_CONTEXT_FIELDS = (
    "age_years",
    "sex",
    "renal_function",
    "hepatic_function",
    "qt_risk",
    "current_inr",
)


def normalize_patient_context(raw: dict[str, Any] | None) -> PatientContext:
    payload = dict(raw or {})
    labs = [_lab(item) for item in _list(payload.get("labs"))]
    conditions = [_condition(item) for item in _list(payload.get("conditions") or payload.get("comorbidities"))]
    genotypes = [_genotype(item) for item in _list(payload.get("genotypes") or payload.get("pgx"))]
    monitoring = [_monitoring(item) for item in _list(payload.get("monitoring"))]

    context = PatientContext(
        age_years=_int_or_none(payload.get("age_years") or payload.get("age")),
        sex=_choice(payload.get("sex"), {"female", "male", "intersex"}, default="unknown"),
        pregnancy_status=_choice(payload.get("pregnancy_status") or payload.get("pregnancy"), {"pregnant", "not_pregnant", "possible"}, default="unknown"),
        renal_function=_choice(
            payload.get("renal_function") or payload.get("renal"),
            {"normal", "mild_impairment", "moderate_impairment", "severe_impairment", "dialysis"},
            default="unknown",
        ),
        hepatic_function=_choice(
            payload.get("hepatic_function") or payload.get("hepatic"),
            {"normal", "mild_impairment", "moderate_impairment", "severe_impairment"},
            default="unknown",
        ),
        qt_risk=_choice(payload.get("qt_risk") or payload.get("qtc"), {"known_long_qt", "possible_qt_risk", "low_risk"}, default="unknown"),
        current_inr=_float_or_none(payload.get("current_inr") or payload.get("inr")),
        labs=labs,
        conditions=conditions,
        genotypes=genotypes,
        monitoring=monitoring,
        concurrent_medications=[str(item).strip() for item in _list(payload.get("concurrent_medications")) if str(item).strip()],
        raw=payload,
    )
    missing = [field for field in _REQUIRED_CONTEXT_FIELDS if _is_missing(context, field)]
    return PatientContext(
        age_years=context.age_years,
        sex=context.sex,
        pregnancy_status=context.pregnancy_status,
        renal_function=context.renal_function,
        hepatic_function=context.hepatic_function,
        qt_risk=context.qt_risk,
        current_inr=context.current_inr,
        labs=context.labs,
        conditions=context.conditions,
        genotypes=context.genotypes,
        monitoring=context.monitoring,
        concurrent_medications=context.concurrent_medications,
        raw=context.raw,
        missing_fields=missing,
    )


def _is_missing(context: PatientContext, field: str) -> bool:
    value = getattr(context, field)
    return value is None or value == "unknown"


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _choice(value: Any, allowed: set[str], *, default: str) -> Any:
    text = str(value or "").strip().lower().replace(" ", "_")
    return text if text in allowed else default


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def _lab(value: Any) -> LabObservation:
    if isinstance(value, dict):
        return LabObservation(
            name=str(value.get("name") or value.get("code") or "lab"),
            value=value.get("value"),
            unit=value.get("unit"),
            interpreted_as=value.get("interpreted_as"),
            observed_at=value.get("observed_at"),
        )
    return LabObservation(name=str(value))


def _condition(value: Any) -> ConditionContext:
    if isinstance(value, dict):
        return ConditionContext(name=str(value.get("name") or value.get("condition") or "condition"), status=str(value.get("status") or "unknown"))
    return ConditionContext(name=str(value))


def _genotype(value: Any) -> GenotypeContext:
    if isinstance(value, dict):
        return GenotypeContext(
            gene=str(value.get("gene") or "unknown"),
            allele_or_variant=str(value.get("allele_or_variant") or value.get("variant") or "unknown"),
            phenotype=value.get("phenotype"),
        )
    return GenotypeContext(gene=str(value), allele_or_variant="unknown")


def _monitoring(value: Any) -> MonitoringState:
    if isinstance(value, dict):
        return MonitoringState(
            parameter=str(value.get("parameter") or value.get("name") or "monitoring"),
            current_value=value.get("current_value"),
            schedule=value.get("schedule"),
        )
    return MonitoringState(parameter=str(value))
