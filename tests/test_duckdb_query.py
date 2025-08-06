import pytest
from src.retrieval.duckdb_query import (
    init_duckdb_connection,
    get_side_effects,
    get_interaction_score,
    get_dili_risk,
    get_dict_rank,
    get_diqt_score,
    get_drug_targets,
)

@pytest.fixture(scope="module")
def duckdb_con():
    return init_duckdb_connection(parquet_dir="data/duckdb")


@ pytest.mark.parametrize("drug, expected_non_empty", [
    ("aspirin", True),
    ("ASPIRIN", True),        # case‐insensitive
    ("unknown_drug_xyz", False)
])
def test_side_effects_basic(drug, expected_non_empty, duckdb_con):
    se = get_side_effects(drug)
    assert isinstance(se, list)
    if expected_non_empty:
        assert len(se) > 0
    else:
        assert se == []


@ pytest.mark.parametrize("d1,d2, expected_zero", [
    ("aspirin", "warfarin", False),
    ("warfarin", "aspirin", False),  # order‐agnostic
    ("foo", "bar", True),
])
def test_interaction_score_various(d1, d2, expected_zero, duckdb_con):
    score = get_interaction_score(d1, d2)
    assert isinstance(score, float)
    if expected_zero:
        assert score == 0.0
    else:
        assert score > 0.0


@ pytest.mark.parametrize("drug", [
    "acetaminophen",
    "ACETAMINOPHEN",     # case‐insensitive
])
def test_dili_risk_known(drug, duckdb_con):
    risk = get_dili_risk(drug)
    assert isinstance(risk, str)
    assert risk in {"low", "medium", "high"}


def test_dili_risk_unknown(duckdb_con):
    assert get_dili_risk("no_such_drug") == ""


@ pytest.mark.parametrize("drug", [
    "ZYPREXA Relprevv",  # from DICTRank sample
    "zyprexa relprevv",  # lowercase
])
def test_dict_rank_known(drug, duckdb_con):
    label = get_dict_rank(drug)
    assert label in {"mild", "moderate", "severe", "unknown"}
    # for this known drug, it should not be 'unknown'
    assert label != "unknown"


def test_dict_rank_unknown(duckdb_con):
    assert get_dict_rank("no_such_drug") == "unknown"


@ pytest.mark.parametrize("drug, has_score", [
    ("cisapride", True),
    ("CISAPRIDE", True),     # case‐insensitive
    ("cloforex", False)      # sample showed NaN → None
])
def test_diqt_score_various(drug, has_score, duckdb_con):
    score = get_diqt_score(drug)
    if has_score:
        assert isinstance(score, float)
    else:
        assert score is None


@ pytest.mark.parametrize("drug, expects_nonempty", [
    ("metformin", True),     # likely has interactions
    ("METFORMIN", True),     # case‐insensitive
    ("no_such_drug", False)
])
def test_drug_targets_various(drug, expects_nonempty, duckdb_con):
    targets = get_drug_targets(drug)
    assert isinstance(targets, list)
    if expects_nonempty:
        assert len(targets) > 0
        # ensure each entry follows the 'id|name|desc' pattern
        assert all(isinstance(t, str) and t.count("|") >= 2 for t in targets)
    else:
        assert targets == []
