from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

EventStatus = Literal["requested", "succeeded", "failed", "skipped"]


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DomainEvent:
    analysis_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    actor: str = "system"
    component: str = "application"
    status: EventStatus = "succeeded"
    input_hash: str | None = None
    output_hash: str | None = None
    source_tool_name: str | None = None
    error_summary: str | None = None
    created_at: str = field(default_factory=utc_now)
    event_id: str = field(default_factory=lambda: "evt_" + uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        analysis_id: str,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        request_id: str | None = None,
        component: str = "application",
        status: EventStatus = "succeeded",
        source_tool_name: str | None = None,
        error_summary: str | None = None,
    ) -> "DomainEvent":
        safe_payload = payload or {}
        return cls(
            analysis_id=analysis_id,
            event_type=event_type,
            payload=safe_payload,
            request_id=request_id,
            component=component,
            status=status,
            input_hash=stable_hash(safe_payload),
            output_hash=stable_hash({"status": status, "error_summary": error_summary}),
            source_tool_name=source_tool_name,
            error_summary=error_summary,
        )

