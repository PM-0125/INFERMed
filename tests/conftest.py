# tests/conftest.py
import os
import shutil
import tempfile
import pytest

@pytest.fixture(scope="session")
def parquet_dir():
    p = os.getenv("DUCKDB_DIR", "").strip()
    if not p:
        pytest.skip("Set DUCKDB_DIR to your parquet folder to run DuckDB tests.")
    if not os.path.isdir(p):
        pytest.skip(f"DUCKDB_DIR not a directory: {p}")
    return p

@pytest.fixture(scope="session")
def tmp_cache_dir():
    d = tempfile.mkdtemp(prefix="infermed_cache_")
    yield d
    shutil.rmtree(d, ignore_errors=True)

@pytest.fixture(scope="session")
def known_drugs():
    # Adjust if your local datasets use slightly different names/casing.
    return ("warfarin", "fluconazole")
