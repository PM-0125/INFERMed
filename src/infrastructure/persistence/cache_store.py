from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.application.events import stable_hash, utc_now


class SQLiteToolCacheStore:
    """Semantic source-result cache for deterministic tools."""

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
                CREATE TABLE IF NOT EXISTS tool_result_cache (
                    cache_key TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    output_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    source_version TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_cache_tool ON tool_result_cache(tool_name, source_name)")

    def key_for(self, *, tool_name: str, tool_version: str, input_payload: dict[str, Any]) -> str:
        return f"tool:{tool_name}:v{tool_version}:input:{stable_hash(input_payload)}"

    def get(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM tool_result_cache WHERE cache_key = ?", (cache_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(
        self,
        *,
        cache_key: str,
        tool_name: str,
        source_name: str,
        input_payload: dict[str, Any],
        payload: dict[str, Any],
        expires_at: str | None = None,
        source_version: str | None = None,
    ) -> None:
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_result_cache
                    (cache_key, tool_name, source_name, input_hash, output_hash, payload_json, created_at, expires_at, source_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    tool_name,
                    source_name,
                    stable_hash(input_payload),
                    stable_hash(payload),
                    payload_json,
                    utc_now(),
                    expires_at,
                    source_version,
                ),
            )
