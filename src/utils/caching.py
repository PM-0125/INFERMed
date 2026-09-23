# src/utils/caching.py
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from contextlib import contextmanager
from contextvars import ContextVar

from src.utils.sqlite_cache import SQLiteCache

# allow only safe chars in filenames; normalize to lowercase
_SAFE = re.compile(r"[^a-z0-9._-]+")
_source_policy = ContextVar('source_cache_policy', default=(False, None))


@contextmanager
def source_cache_policy(*, force_refresh=False, max_age_s=None):
    """Request-local cache policy; copy context when dispatching source workers."""
    token = _source_policy.set((force_refresh, max_age_s))
    try:
        yield
    finally:
        _source_policy.reset(token)


def _effective_ttl(ttl):
    _, max_age = _source_policy.get()
    return ttl if max_age is None else min(ttl, max_age) if ttl is not None else max_age

def sanitize_key(key: str) -> str:
    return _SAFE.sub("_", key.lower())

def ensure_dir(root: str | Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return root

def cache_file_path(root: str | Path, key: str, ext: str = "json") -> Path:
    root = ensure_dir(root)
    return root / f"{sanitize_key(key)}.{ext}"


def _sqlite_cache_config() -> tuple[bool, str]:
    """Return whether SQLite cache is enabled and where it should live."""
    try:
        from src.config.settings import get_settings

        settings = get_settings()
        backend = settings.cache_backend
        path = settings.sqlite_cache_path
    except Exception:
        backend = os.getenv("CACHE_BACKEND", "file")
        path = os.getenv("SQLITE_CACHE_PATH", "data/cache/infermed_cache.sqlite")

    return backend.strip().lower() == "sqlite", path


def _sqlite_key(root: str | Path, key: str, ext: str, kind: str) -> str:
    root_key = sanitize_key(Path(root).as_posix())
    return f"{kind}:{root_key}:{sanitize_key(key)}.{ext}"


def _sqlite_cache() -> SQLiteCache | None:
    enabled, path = _sqlite_cache_config()
    return SQLiteCache(path) if enabled else None

# -------- JSON --------

def load_json(root: str | Path, key: str, *, ttl: Optional[int] = None, ext: str = "json") -> Optional[dict]:
    """
    Load JSON from cache, optionally enforcing a TTL (in seconds).
    Returns None on cache miss, expired entry, or corrupt JSON (corrupt file is removed).
    """
    if _source_policy.get()[0]:
        return None
    ttl = _effective_ttl(ttl)
    sqlite_cache = _sqlite_cache()
    if sqlite_cache is not None:
        cached = sqlite_cache.get_json(_sqlite_key(root, key, ext, "json"), ttl=ttl)
        return cached if isinstance(cached, dict) else None

    p = cache_file_path(root, key, ext=ext)
    if not p.exists():
        return None
    if ttl is not None:
        try:
            if (time.time() - p.stat().st_mtime) > ttl:
                return None
        except Exception:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        try:
            p.unlink()
        except Exception:
            pass
        return None

def save_json(root: str | Path, key: str, obj: Any, *, ext: str = "json") -> Path:
    """
    Atomically write JSON to cache (write to .tmp then os.replace).
    """
    sqlite_cache = _sqlite_cache()
    if sqlite_cache is not None:
        sqlite_cache.set_json(
            _sqlite_key(root, key, ext, "json"),
            obj,
            kind="json",
            metadata={"root": str(root), "ext": ext},
        )
        return Path(sqlite_cache.path)

    p = cache_file_path(root, key, ext=ext)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, p)
    return p

# -------- TEXT (for small summaries or blobs) --------

def load_text(root: str | Path, key: str, *, ttl: Optional[int] = None, ext: str = "json") -> Optional[str]:
    if _source_policy.get()[0]:
        return None
    ttl = _effective_ttl(ttl)
    sqlite_cache = _sqlite_cache()
    if sqlite_cache is not None:
        return sqlite_cache.get_text(_sqlite_key(root, key, ext, "text"), ttl=ttl)

    p = cache_file_path(root, key, ext=ext)
    if not p.exists():
        return None
    if ttl is not None:
        try:
            if (time.time() - p.stat().st_mtime) > ttl:
                return None
        except Exception:
            return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        try:
            p.unlink()
        except Exception:
            pass
        return None

def save_text(root: str | Path, key: str, text: str, *, ext: str = "json") -> Path:
    sqlite_cache = _sqlite_cache()
    if sqlite_cache is not None:
        sqlite_cache.set_text(
            _sqlite_key(root, key, ext, "text"),
            text,
            kind="text",
            metadata={"root": str(root), "ext": ext},
        )
        return Path(sqlite_cache.path)

    p = cache_file_path(root, key, ext=ext)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)
    return p
