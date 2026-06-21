from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Validation limits
DRUG_NAME_MAX_LENGTH: int = 120
QUESTION_MAX_LENGTH: int = 1000

AudienceMode = Literal["doctor", "patient", "pv_research"]
ConversationRole = Literal["user", "assistant"]
AnalysisDepth = Literal["standard", "deep_research"]


class AnalyzeRequest(BaseModel):
    drugs: list[str] = Field(min_length=2)
    mode: AudienceMode = "doctor"
    refreshEvidence: bool = False

    @field_validator("drugs")
    @classmethod
    def validate_drugs(cls, value: list[str]) -> list[str]:
        return _normalize_drugs(value)


class MedicationEntry(BaseModel):
    text: str = Field(min_length=1, max_length=DRUG_NAME_MAX_LENGTH)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        cleaned = _reject_control_chars(value.strip(), "medication")
        if not cleaned:
            raise ValueError("Medication text cannot be empty")
        return " ".join(cleaned.split())


class MedicationSetAnalyzeRequest(BaseModel):
    medications: list[MedicationEntry] = Field(min_length=2)
    audience: AudienceMode = "doctor"
    patient_context: Optional[dict[str, Any]] = None
    refresh_evidence: bool = False
    analysis_depth: AnalysisDepth = "standard"

    @property
    def medication_texts(self) -> list[str]:
        return [item.text for item in self.medications]


class ConversationTurn(BaseModel):
    role: ConversationRole
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        cleaned = _reject_control_chars(value.strip(), "conversation text")
        if not cleaned:
            raise ValueError("Conversation text cannot be empty")
        return cleaned


class FollowUpRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUESTION_MAX_LENGTH)
    drugs: list[str] = Field(min_length=2)
    mode: AudienceMode = "doctor"
    contextId: Optional[str] = None
    history: list[ConversationTurn] = Field(default_factory=list, max_length=6)
    priorAssessment: Optional[str] = Field(default=None, max_length=4000)
    followUpCount: int = Field(default=0, ge=0, le=3)

    @field_validator("drugs")
    @classmethod
    def validate_drugs(cls, value: list[str]) -> list[str]:
        return _normalize_drugs(value)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        cleaned = _reject_control_chars(value.strip(), "question")
        if not cleaned:
            raise ValueError("Question cannot be empty")
        return cleaned

    @field_validator("priorAssessment")
    @classmethod
    def validate_prior_assessment(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = _reject_control_chars(value.strip(), "prior assessment")
        return cleaned or None


def _normalize_drugs(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        value = _reject_control_chars(str(item).strip(), "drug")
        if not value:
            continue
        if len(value) > DRUG_NAME_MAX_LENGTH:
            raise ValueError("Drug name too long")
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(" ".join(value.split()))
    if len(cleaned) < 2:
        raise ValueError("At least two non-empty drugs are required")
    return cleaned


def _reject_control_chars(value: str, field_name: str) -> str:
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field_name} contains control characters")
    return value
