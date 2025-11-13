# tests/test_duckdb_query.py
import os
import pytest

from src.retrieval.duckdb_query import DuckDBClient, init_duckdb_connection

# Point to your parquet directory
PARQUET_DIR = os.environ.get("DUCKDB_DIR", "data/duckdb")

@pytest.fixture(scope="module")
def client():
    # will raise if dir is missing
    init_duckdb_connection(PARQUET_DIR)
    return DuckDBClient(PARQUET_DIR)

@pytest.mark.parametrize("drug, expect_list", [
    ("warfarin", True),
    ("fluconazole", True),
    ("__no_such_drug__", True),   # still a list, may be empty
])
def test_get_side_effects_single(client, drug, expect_list):
    out = client.get_side_effects(drug)
    assert isinstance(out, list)
    # not asserting non-empty because local TwoSides coverage varies

def test_get_side_effects_batch(client):
    out = client.get_side_effects(["warfarin", "fluconazole"])
    assert isinstance(out, dict)
    assert "warfarin" in out and "fluconazole" in out
    assert isinstance(out["warfarin"], list)

@pytest.mark.parametrize("a,b", [
    ("warfarin", "fluconazole"),
    ("fluconazole", "warfarin"),   # order-agnostic
])
def test_get_interaction_score(client, a, b):
    score = client.get_interaction_score(a, b)
    assert isinstance(score, float)

@pytest.mark.parametrize("drug", ["warfarin", "fluconazole", "__no_such__"])
def test_get_dili_risk(client, drug):
    out = client.get_dili_risk(drug)
    assert isinstance(out, str) or out is None  # "" when not found in your impl

def test_get_dili_risk_batch(client):
    out = client.get_dili_risk(["warfarin", "fluconazole"])
    assert isinstance(out, dict)
    assert set(out.keys()) == {"warfarin", "fluconazole"}

@pytest.mark.parametrize("drug", ["warfarin", "fluconazole", "__no__"])
def test_get_dict_rank(client, drug):
    out = client.get_dict_rank(drug)
    assert isinstance(out, str)

def test_get_dict_rank_batch(client):
    out = client.get_dict_rank(["warfarin", "fluconazole"])
    assert isinstance(out, dict)
    assert set(out.keys()) == {"warfarin", "fluconazole"}

@pytest.mark.parametrize("drug", ["cisapride", "warfarin", "__no__"])
def test_get_diqt_score(client, drug):
    out = client.get_diqt_score(drug)
    assert (out is None) or isinstance(out, (int, float))

def test_get_diqt_score_batch(client):
    out = client.get_diqt_score(["cisapride", "warfarin"])
    assert isinstance(out, dict)
    assert set(out.keys()) == {"cisapride", "warfarin"}

@pytest.mark.parametrize("drug", ["warfarin", "fluconazole", "__no__"])
def test_get_drug_targets(client, drug):
    out = client.get_drug_targets(drug)
    assert isinstance(out, list)
    # values are strings like "id|name|desc" (or empty if not found)
    if out:
        assert all(isinstance(x, str) for x in out)

def test_get_drug_targets_batch(client):
    out = client.get_drug_targets(["warfarin", "fluconazole"])
    assert isinstance(out, dict)
    assert "warfarin" in out and "fluconazole" in out
    assert isinstance(out["warfarin"], list)
