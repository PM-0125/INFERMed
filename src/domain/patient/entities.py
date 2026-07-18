from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Sex = Literal["female", "male", "intersex", "unknown"]
PregnancyStatus = Literal["pregnant", "not_pregnant", "possible", "unknown"]
RenalFunctionCategory = Literal["normal", "mild_impairment", "moderate_impairment", "severe_impairment", "dialysis", "unknown"]
HepaticFunctionCategory = Literal["normal", "mild_impairment", "moderate_impairment", "severe_impairment", "unknown"]
QTRiskCategory = Literal["known_long_qt", "possible_qt_risk", "low_risk", "unknown"]


@dataclass(frozen=True)
class LabObservation:
    name: str
    value: str | float | int | None = None
    unit: str | None = None
    interpreted_as: str | None = None
    observed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConditionContext:
    name: str
    status: str = "unknown"
    source: str = "user"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenotypeContext:
    gene: str
    allele_or_variant: str
    phenotype: str | None = None
    source: str = "user"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MonitoringState:
    parameter: str
    current_value: str | None = None
    schedule: str | None = None
    source: str = "user"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PatientContext:
    age_years: int | None = None
    sex: Sex = "unknown"
    pregnancy_status: PregnancyStatus = "unknown"
    renal_function: RenalFunctionCategory = "unknown"
    hepatic_function: HepaticFunctionCategory = "unknown"
    qt_risk: QTRiskCategory = "unknown"
    current_inr: float | None = None
    labs: list[LabObservation] = field(default_factory=list)
    conditions: list[ConditionContext] = field(default_factory=list)
    genotypes: list[GenotypeContext] = field(default_factory=list)
    monitoring: list[MonitoringState] = field(default_factory=list)
    concurrent_medications: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def has_patient_specific_data(self) -> bool:
        return bool(
            self.age_years is not None
            or self.sex != "unknown"
            or self.renal_function != "unknown"
            or self.hepatic_function != "unknown"
            or self.qt_risk != "unknown"
            or self.current_inr is not None
            or self.conditions
            or self.genotypes
        )
