import json
import threading
from concurrent.futures import ThreadPoolExecutor
from src.utils import caching


def test_same_key_concurrent_writers_leave_complete_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(caching, "_sqlite_cache", lambda: None)
    original = caching.os.replace
    barrier = threading.Barrier(2)

    def simultaneous_replace(*args, **kwargs):
        barrier.wait(timeout=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(caching.os, "replace", simultaneous_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(caching.save_json, tmp_path, "same", {"value": i})
            for i in (1, 2)
        ]
        paths = [future.result() for future in futures]
    assert paths[0] == paths[1]
    assert json.loads(paths[0].read_text()) in [{"value": 1}, {"value": 2}]
