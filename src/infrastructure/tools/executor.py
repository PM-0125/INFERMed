from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.application.source_tools import SourceTool, SourceToolInput, SourceToolResult
from src.infrastructure.persistence.cache_store import SQLiteToolCacheStore
from src.domain.evidence.entities import EvidenceCard


@dataclass
class CachedToolExecutor:
    cache_store: SQLiteToolCacheStore

    def execute(
        self, tool: SourceTool, tool_input: SourceToolInput, *, refresh: bool = False
    ) -> SourceToolResult:
        cache_key = self.cache_store.key_for(
            tool_name=tool_input.tool_name,
            tool_version=tool_input.source_version,
            input_payload=tool_input.canonical_payload(),
        )
        if not refresh:
            cached = self.cache_store.get(cache_key)
            if cached is not None and cached.get("schema_version") == 2:
                return SourceToolResult(
                    tool_name=tool_input.tool_name,
                    source_name=tool_input.source_name,
                    ok=bool(cached.get("ok", True)),
                    normalized_payload=cached.get("normalized_payload") or cached,
                    error=cached.get("error"),
                    from_cache=True,
                    evidence_cards=[
                        EvidenceCard(**row) for row in cached.get("evidence_cards", [])
                    ],
                    raw_payload_hash=cached.get("raw_payload_hash"),
                )

        result = tool.execute(tool_input)
        if not result.ok:
            return result
        self.cache_store.put(
            cache_key=cache_key,
            tool_name=tool_input.tool_name,
            source_name=tool_input.source_name,
            input_payload=tool_input.canonical_payload(),
            payload={
                "schema_version": 2,
                "evidence_cards": [card.to_dict() for card in result.evidence_cards],
                "ok": result.ok,
                "normalized_payload": result.normalized_payload,
                "raw_payload_hash": result.raw_payload_hash,
                "error": result.error,
                "evidence_card_count": len(result.evidence_cards),
            },
            source_version=tool_input.source_version,
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        )
        return result
