from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.application.events import DomainEvent, utc_now
from src.domain.evidence.entities import EvidenceCard


class SQLiteEventStore:
    """SQLite-backed analysis/event/evidence audit store.

    This store intentionally lives beside the cache tables but uses separate
    tables. It is append-friendly and queryable for audit/debug workflows.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_run (
                    analysis_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    normalized_drug_set_key TEXT NOT NULL,
                    data_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS domain_event (
                    event_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    request_id TEXT,
                    actor TEXT NOT NULL,
                    component TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_hash TEXT,
                    output_hash TEXT,
                    source_tool_name TEXT,
                    error_summary TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_card (
                    evidence_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    claim_text TEXT NOT NULL,
                    evidence_grade TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS explanation_record (
                    explanation_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    model_name TEXT,
                    prompt_hash TEXT,
                    output_hash TEXT,
                    answer_text TEXT NOT NULL,
                    verification_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_snapshot (
                    analysis_id TEXT PRIMARY KEY,
                    context_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    response_read_model_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_event_analysis ON domain_event(analysis_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_card_analysis ON evidence_card(analysis_id)")

    def create_analysis_run(
        self,
        *,
        analysis_id: str,
        request_hash: str,
        normalized_drug_set_key: str,
        data_mode: str,
        status: str = "running",
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_run
                    (analysis_id, request_hash, normalized_drug_set_key, data_mode, created_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    status = excluded.status
                """,
                (analysis_id, request_hash, normalized_drug_set_key, data_mode, now, now, status),
            )

    def update_analysis_status(self, analysis_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE analysis_run SET status = ?, updated_at = ? WHERE analysis_id = ?",
                (status, utc_now(), analysis_id),
            )

    def append_event(self, event: DomainEvent) -> None:
        payload_json = json.dumps(event.payload, sort_keys=True, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO domain_event
                    (
                        event_id, analysis_id, event_type, payload_json, request_id, actor,
                        component, status, input_hash, output_hash, source_tool_name,
                        error_summary, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.analysis_id,
                    event.event_type,
                    payload_json,
                    event.request_id,
                    event.actor,
                    event.component,
                    event.status,
                    event.input_hash,
                    event.output_hash,
                    event.source_tool_name,
                    event.error_summary,
                    event.created_at,
                ),
            )

    def append_evidence_card(self, card: EvidenceCard) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO evidence_card
                    (
                        evidence_id, analysis_id, source_name, source_type, claim_type,
                        claim_text, evidence_grade, provenance_json, payload_json, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card.evidence_id,
                    card.analysis_id,
                    card.source_name,
                    card.source_type,
                    card.claim_type,
                    card.claim_text,
                    card.evidence_grade,
                    json.dumps(card.provenance, sort_keys=True, ensure_ascii=False, default=str),
                    json.dumps(card.payload, sort_keys=True, ensure_ascii=False, default=str),
                    utc_now(),
                ),
            )

    def append_explanation_record(
        self,
        *,
        explanation_id: str,
        analysis_id: str,
        answer_text: str,
        verification: dict[str, Any],
        model_name: str | None = None,
        prompt_hash: str | None = None,
        output_hash: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO explanation_record
                    (
                        explanation_id, analysis_id, model_name, prompt_hash,
                        output_hash, answer_text, verification_json, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    explanation_id,
                    analysis_id,
                    model_name,
                    prompt_hash,
                    output_hash,
                    answer_text,
                    json.dumps(verification, sort_keys=True, ensure_ascii=False, default=str),
                    utc_now(),
                ),
            )

    def append_analysis_snapshot(
        self,
        *,
        analysis_id: str,
        context: dict[str, Any],
        decision: dict[str, Any],
        response_read_model: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_snapshot
                    (
                        analysis_id, context_json, decision_json,
                        response_read_model_json, created_at, updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    context_json = excluded.context_json,
                    decision_json = excluded.decision_json,
                    response_read_model_json = excluded.response_read_model_json,
                    updated_at = excluded.updated_at
                """,
                (
                    analysis_id,
                    json.dumps(context, sort_keys=True, ensure_ascii=False, default=str),
                    json.dumps(decision, sort_keys=True, ensure_ascii=False, default=str),
                    json.dumps(response_read_model, sort_keys=True, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )

    def get_analysis_snapshot(self, analysis_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT context_json, decision_json, response_read_model_json, updated_at
                FROM analysis_snapshot
                WHERE analysis_id = ?
                """,
                (analysis_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "context": json.loads(row[0]),
            "decision": json.loads(row[1]),
            "response_read_model": json.loads(row[2]),
            "updated_at": row[3],
        }

    def list_events(self, analysis_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_type, payload_json, status, source_tool_name, error_summary, created_at
                FROM domain_event
                WHERE analysis_id = ?
                ORDER BY created_at, event_id
                """,
                (analysis_id,),
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "event_type": row[1],
                "payload": json.loads(row[2]),
                "status": row[3],
                "source_tool_name": row[4],
                "error_summary": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]
