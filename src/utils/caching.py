# src/utils/caching.py
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

# allow only safe chars in filenames; normalize to lowercase
_SAFE = re.compile(r"[^a-z0-9._-]+")

def sanitize_key(key: str) -> str:
    return _SAFE.sub("_", key.lower())

def ensure_dir(root: str | Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return root

def cache_file_path(root: str | Path, key: str, ext: str = "json") -> Path:
    root = ensure_dir(root)
    return root / f"{sanitize_key(key)}.{ext}"

# -------- JSON --------

def load_json(root: str | Path, key: str, *, ttl: Optional[int] = None, ext: str = "json") -> Optional[dict]:
    """
    Load JSON from cache, optionally enforcing a TTL (in seconds).
    Returns None on cache miss, expired entry, or corrupt JSON (corrupt file is removed).
    """
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
    p = cache_file_path(root, key, ext=ext)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, p)
    return p

# -------- TEXT (for small summaries or blobs) --------

def load_text(root: str | Path, key: str, *, ttl: Optional[int] = None, ext: str = "json") -> Optional[str]:
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
    p = cache_file_path(root, key, ext=ext)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)
    return p
