from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class SQLiteCache:
    """Small durable cache for API/source payloads.

    The cache stores JSON-serializable payloads keyed by a namespaced string.
    It is intentionally dependency-free so it can run on the deployment host
    without Redis or another service.
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
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_entries_kind ON cache_entries(kind)"
            )

    def get_json(self, key: str, *, ttl: int | None = None) -> Any | None:
        row = self._get_row(key)
        if row is None:
            return None
        payload, updated_at = row
        if ttl is not None and (time.time() - float(updated_at)) > ttl:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            self.delete(key)
            return None

    def set_json(
        self,
        key: str,
        obj: Any,
        *,
        kind: str = "json",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        payload = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cache_entries
                    (cache_key, kind, payload, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    kind = excluded.kind,
                    payload = excluded.payload,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (key, kind, payload, metadata_json, now, now),
            )

    def get_text(self, key: str, *, ttl: int | None = None) -> str | None:
        value = self.get_json(key, ttl=ttl)
        return value if isinstance(value, str) else None

    def set_text(
        self,
        key: str,
        text: str,
        *,
        kind: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.set_json(key, text, kind=kind, metadata=metadata)

    def delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))

    def clear_prefix(self, prefix: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM cache_entries WHERE cache_key LIKE ?",
                (f"{prefix}%",),
            )
            return int(cursor.rowcount or 0)

    def _get_row(self, key: str) -> tuple[str, float] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM cache_entries WHERE cache_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return str(row[0]), float(row[1])
