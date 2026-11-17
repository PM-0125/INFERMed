"""Offline regressions for source scope and assay interpretation."""

from types import SimpleNamespace

import pytest

from src.retrieval import chembl_client, openfda_api


@pytest.fixture
def faers_client():
    # These tests replace the external fetch/cache boundary; no disk or session needed.
    return openfda_api.OpenFDAClient.__new__(openfda_api.OpenFDAClient)


def test_empty_pair_query_does_not_turn_single_drug_counts_into_pair_evidence(
    faers_client, monkeypatch
):
    def counts(query):
        if query.suffix == "combo":
            return {}
        return {"haemorrhage": 5389 if query.drug == "a" else 300}

    monkeypatch.setattr(faers_client, "_fetch_and_cache_counts", counts)
    assert faers_client.get_combination_reactions("a", "b") == []
    assert faers_client.get_pair_reaction_evidence("a", "b") == []


def test_observed_pair_counts_remain_pair_evidence(faers_client, monkeypatch):
    monkeypatch.setattr(
        faers_client, "_fetch_and_cache_counts", lambda query: {"rash": 2, "nausea": 7}
    )
    evidence = faers_client.get_pair_reaction_evidence("a", "b", top_k=1)
    assert len(evidence) == 1
    assert evidence[0].subject == "a + b"
    assert evidence[0].predicate == "reported_combination_reaction"
    assert evidence[0].object == "nausea"
    assert evidence[0].raw["count"] == 7


@pytest.fixture
def activities(monkeypatch):
    records = []
    monkeypatch.setattr(
        chembl_client,
        "_get_compound_by_name",
        lambda name: {"molecule_chembl_id": "CHEMBL_TEST"},
    )
    monkeypatch.setattr(chembl_client, "_chembl_timeout", lambda: 1)
    monkeypatch.setattr(
        chembl_client.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"activities": records}
        ),
    )
    return records


@pytest.mark.parametrize("endpoint", ["Ki", "IC50", "EC50"])
@pytest.mark.parametrize("value,units", [("0.5", "uM"), ("500", "nM"), ("0.5", None)])
def test_potency_does_not_establish_enzyme_role_or_clinical_strength(
    activities, endpoint, value, units
):
    activities.append(
        {
            "target_pref_name": "Cytochrome P450 3A4",
            "standard_type": endpoint,
            "standard_value": value,
            "standard_units": units,
            "standard_relation": "=",
            "assay_chembl_id": "ASSAY_TEST",
            "assay_description": "Cell-free assay",
            "target_organism": "Homo sapiens",
        }
    )
    interaction = chembl_client.get_enzyme_interactions("test")[0]
    assert interaction["action"] == "unknown"
    assert interaction["potency_type"] == endpoint
    assert interaction["potency_value"] == value
    assert interaction["potency_units"] == units
    assert interaction["assay_chembl_id"] == "ASSAY_TEST"
    assert interaction["assay_description"] == "Cell-free assay"
    assert interaction["target_organism"] == "Homo sapiens"
    enriched = chembl_client.enrich_mechanistic_data("test", {"inhibitor": ["cyp3a4"]})
    assert enriched["enzyme_strength"] == {"strong": [], "moderate": [], "weak": []}
    assert enriched["chembl_validation"]["matches"] == []
    assert enriched["bioactivities"] == [interaction]


@pytest.mark.parametrize("action", ["INHIBITOR", "SUBSTRATE", "INDUCER"])
def test_explicit_assay_action_is_retained_without_clinical_strength(
    activities, action
):
    activities.append(
        {
            "target_pref_name": "CYP3A4",
            "standard_type": "EC50",
            "standard_value": "0.1",
            "standard_units": "nM",
            "action_type": action,
        }
    )
    interaction = chembl_client.get_enzyme_interactions("test")[0]
    assert interaction["action"] == action.lower()
    assert interaction["action_basis"] == "explicit_assay_action_type"
    enriched = chembl_client.enrich_mechanistic_data(
        "test", {action.lower(): ["cyp3a4"]}
    )
    assert enriched["enzyme_strength"] == {"strong": [], "moderate": [], "weak": []}
    assert enriched["chembl_validation"]["matches"] == ["cyp3a4"]


def test_explicit_role_does_not_validate_a_different_role(activities):
    activities.append(
        {
            "target_pref_name": "CYP3A4",
            "standard_type": "EC50",
            "standard_value": "0.1",
            "action_type": "SUBSTRATE",
        }
    )
    enriched = chembl_client.enrich_mechanistic_data("test", {"inhibitor": ["cyp3a4"]})
    assert enriched["chembl_validation"]["matches"] == []


@pytest.mark.parametrize(
    "endpoint,value", [("Ki", "0.1"), ("EC50", "500"), (None, None)]
)
def test_transporter_assays_do_not_default_to_substrate_or_inhibitor(
    activities, endpoint, value
):
    activities.append(
        {
            "target_pref_name": "P-glycoprotein",
            "standard_type": endpoint,
            "standard_value": value,
            "standard_units": "nM",
        }
    )
    result = chembl_client.get_transporter_data("test")[0]
    assert result["action"] == "unknown"
    assert result["potency_type"] == endpoint
    assert result["potency_value"] == value
    assert result["potency_units"] == "nM"
