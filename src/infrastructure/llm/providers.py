from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMRequest:
    context: dict[str, Any]
    audience: str
    question: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    stream: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse:
        ...
