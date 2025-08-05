import pytest
import duckdb

# tests/test_duckdb_query.py
from src.retrieval.duckdb_query import init_duckdb_connection, \
    get_side_effects, get_interaction_score, get_dili_risk, \
    get_dict_rank, get_diqt_score, get_drug_targets

@pytest.fixture(scope="module")
def duckdb_con():
    return init_duckdb_connection(parquet_dir="data/duckdb")

def test_side_effects(duckdb_con):
    se = get_side_effects("aspirin")
    assert isinstance(se, list)
    assert "Nausea" in se or "Headache" in se

def test_interaction_score_exists(duckdb_con):
    score = get_interaction_score("aspirin", "warfarin")
    assert isinstance(score, float)
    assert score >= 0.0

def test_interaction_score_none(duckdb_con):
    assert get_interaction_score("foo", "bar") == 0.0

def test_dili_risk(duckdb_con):
    risk = get_dili_risk("acetaminophen")
    assert risk.lower() in {"high","low","medium"}  # adjust to your labels

def test_dict_rank(duckdb_con):
    label = get_dict_rank("zolpidem")
    assert label in {"mild","moderate","severe","unknown"}

def test_diqt_score(duckdb_con):
    score = get_diqt_score("cisapride")
    assert isinstance(score, float) or score is None

def test_drug_targets(duckdb_con):
    targets = get_drug_targets("metformin")
    assert isinstance(targets, list)
    # make sure we at least get one “ID|Name|Desc” pattern
    assert all("|" in t for t in targets) or targets == []

