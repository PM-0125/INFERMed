from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor

from src.utils import caching


def test_refresh_is_scoped_and_reaches_source_workers(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "_sqlite_cache", lambda: None)
    caching.save_json(tmp_path, "source", {"ok": True})
    with caching.source_cache_policy(force_refresh=True, max_age_s=3600):
        assert caching.load_json(tmp_path, "source") is None
        with ThreadPoolExecutor(1) as pool:
            assert (
                pool.submit(
                    copy_context().run, caching.load_json, tmp_path, "source"
                ).result()
                is None
            )
    assert caching.load_json(tmp_path, "source") == {"ok": True}


def test_source_policy_caps_longer_ttl(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(caching, "_sqlite_cache", lambda: None)
    path = caching.save_json(tmp_path, "source", {"ok": True})
    old = time.time() - 7200
    os.utime(path, (old, old))
    with caching.source_cache_policy(max_age_s=3600):
        assert caching.load_json(tmp_path, "source", ttl=86400) is None


def test_changed_snapshots_are_appended_without_double_counting(tmp_path):
    from src.utils.sqlite_cache import SQLiteCache

    cache = SQLiteCache(tmp_path / "cache.sqlite")
    cache.set_json("faers:pair", {"count": 10})
    cache.set_json("faers:pair", {"count": 12})
    cache.set_json("faers:pair", {"count": 12})
    assert cache.get_json("faers:pair") == {"count": 12}
    with cache._connect() as conn:
        rows = conn.execute("SELECT payload FROM cache_history").fetchall()
    assert rows == [('{"count": 10}',)]


def test_context_ttl_uses_original_timestamp(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime, timezone, timedelta
    from src.llm import rag_pipeline as rag

    monkeypatch.setattr(
        rag, "get_settings", lambda: SimpleNamespace(evidence_cache_ttl_hours=24)
    )
    path = tmp_path / "context.json"
    path.write_text("{}")
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert not rag._context_cache_fresh({"meta": {"evidence_assembled_at": old}}, path)
    assert rag._context_cache_fresh({"meta": {}}, path)


def test_expired_and_forced_contexts_rebuild(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime, timezone, timedelta
    import json
    from src.llm import rag_pipeline as rag

    monkeypatch.setattr(rag, "CTX_DIR", str(tmp_path))
    monkeypatch.setattr(
        rag,
        "get_settings",
        lambda: SimpleNamespace(evidence_cache_ttl_hours=24, cache_backend="file"),
    )
    calls = []

    def retrieve(*args, **kwargs):
        calls.append(caching._source_policy.get())
        return {"meta": {"version": rag.VERSION}, "new_evidence": len(calls)}

    monkeypatch.setattr(rag, "retrieve_and_normalize", retrieve)
    ctx, key = rag.get_context_cached("a", "b")
    rag.get_context_cached("a", "b")
    assert len(calls) == 1
    ctx["meta"]["evidence_assembled_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    (tmp_path / (key + ".json")).write_text(json.dumps(ctx))
    assert rag.get_context_cached("a", "b")[0]["new_evidence"] == 2
    assert rag.get_context_cached("a", "b", force_refresh=True)[0]["new_evidence"] == 3
    assert calls == [(False, 86400), (False, 86400), (True, 86400)]
