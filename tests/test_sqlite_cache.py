from __future__ import annotations

import time

from src.utils.caching import load_json, load_text, save_json, save_text
from src.utils.sqlite_cache import SQLiteCache


def test_sqlite_cache_roundtrip_json(tmp_path):
    cache = SQLiteCache(tmp_path / "cache.sqlite")

    cache.set_json("openfda:drug", {"count": 2})

    assert cache.get_json("openfda:drug") == {"count": 2}


def test_sqlite_cache_roundtrip_text(tmp_path):
    cache = SQLiteCache(tmp_path / "cache.sqlite")

    cache.set_text("summary:drug", "cached summary")

    assert cache.get_text("summary:drug") == "cached summary"


def test_sqlite_cache_ttl_expiry(tmp_path, monkeypatch):
    cache = SQLiteCache(tmp_path / "cache.sqlite")
    cache.set_json("old", {"value": True})

    original_time = time.time()
    monkeypatch.setattr("src.utils.sqlite_cache.time.time", lambda: original_time + 5)

    assert cache.get_json("old", ttl=1) is None


def test_file_cache_helpers_can_use_sqlite_backend(tmp_path, monkeypatch):
    sqlite_path = tmp_path / "infermed_cache.sqlite"
    monkeypatch.setenv("CACHE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_CACHE_PATH", str(sqlite_path))

    save_json(tmp_path / "openfda", "Warfarin", {"ok": True})
    save_text(tmp_path / "openfda", "summary", "hello")

    assert load_json(tmp_path / "openfda", "Warfarin") == {"ok": True}
    assert load_text(tmp_path / "openfda", "summary") == "hello"
    assert sqlite_path.exists()
