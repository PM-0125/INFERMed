from __future__ import annotations

from pathlib import Path
from typing import Any

from src.domain.evidence.entities import EvidenceCard
from src.infrastructure.persistence.event_store import SQLiteEventStore


class SQLiteEvidenceStore:
    """Evidence-card persistence adapter around the audit store."""

    def __init__(self, path: str | Path):
        self._store = SQLiteEventStore(path)

    def append(self, card: EvidenceCard) -> None:
        self._store.append_evidence_card(card)

    def list_for_analysis(self, analysis_id: str) -> list[dict[str, Any]]:
        # Evidence cards are currently written through SQLiteEventStore. Add a
        # dedicated query here when the UI needs evidence replay independent of
        # analysis snapshots.
        snapshot = self._store.get_analysis_snapshot(analysis_id)
        if not snapshot:
            return []
        response = snapshot.get("response_read_model") or {}
        return response.get("evidence_cards") or []
