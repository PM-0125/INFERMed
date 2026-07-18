from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.retrieval.dailymed_client import DailyMedClient
from src.retrieval.openfda_label_client import OpenFDALabelClient
from src.retrieval.research_api_clients import (
    BioGRIDClient,
    DrugCentralClient,
    EuropePMCClient,
    FDAPGxClient,
    OpenTargetsClient,
    StringDBClient,
)
from src.retrieval.rxnorm_client import RxNormClient


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any]
    text: str = ""

    def json(self) -> dict[str, Any]:
        return self.payload


def test_rxnorm_client_normalizes_identity_and_classes(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params or {}))
        if url.endswith("/rxcui.json"):
            return FakeResponse(200, {"idGroup": {"rxnormId": ["11289"]}})
        if url.endswith("/rxcui/11289/properties.json"):
            return FakeResponse(200, {"properties": {"rxcui": "11289", "name": "warfarin", "tty": "IN", "synonym": ""}})
        if url.endswith("/rxcui/11289/related.json"):
            return FakeResponse(
                200,
                {
                    "relatedGroup": {
                        "conceptGroup": [
                            {"conceptProperties": [{"rxcui": "11289", "name": "warfarin", "tty": "IN"}]}
                        ]
                    }
                },
            )
        if url.endswith("/rxclass/class/byRxcui.json"):
            return FakeResponse(
                200,
                {
                    "rxclassDrugInfoList": {
                        "rxclassDrugInfo": [
                            {
                                "rxclassMinConceptItem": {
                                    "classId": "B01AA",
                                    "className": "Vitamin K antagonists",
                                    "classType": "ATC4",
                                },
                                "rela": "has_ATC",
                            }
                        ]
                    }
                },
            )
        return FakeResponse(404, {})

    client = RxNormClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client._session, "get", fake_get)

    payload = client.resolve_drug("warfarin")

    assert payload["resolved"] is True
    assert payload["rxcui"] == "11289"
    assert payload["ingredients"][0]["name"] == "warfarin"
    assert payload["classes"][0]["class_name"] == "Vitamin K antagonists"

    cached = client.resolve_drug("warfarin")
    assert cached == payload
    assert len(calls) == 5


def test_openfda_label_client_normalizes_sections(monkeypatch, tmp_path):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse(
            200,
            {
                "results": [
                    {
                        "id": "label-1",
                        "set_id": "set-1",
                        "effective_time": "20240101",
                        "openfda": {"generic_name": ["WARFARIN"], "rxcui": ["11289"]},
                        "drug_interactions": ["CYP2C9 inhibitor text."],
                        "warnings_and_cautions": ["Bleeding warning."],
                    }
                ]
            },
        )

    client = OpenFDALabelClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client._session, "get", fake_get)

    payload = client.get_label("warfarin")

    assert payload["found"] is True
    assert payload["rxcui"] == ["11289"]
    assert payload["sections"]["drug_interactions"] == "CYP2C9 inhibitor text."
    assert payload["sections"]["warnings_and_cautions"] == "Bleeding warning."


def test_dailymed_client_normalizes_spl_metadata(monkeypatch, tmp_path):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse(
            200,
            {
                "data": [
                    {
                        "setid": "set-1",
                        "title": "WARFARIN tablet",
                        "spl_version": "3",
                        "published_date": "2024-01-01",
                    }
                ]
            },
        )

    client = DailyMedClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client._session, "get", fake_get)

    payload = client.get_spl_metadata("warfarin")

    assert payload["found"] is True
    assert payload["records"][0]["set_id"] == "set-1"
    assert payload["records"][0]["title"] == "WARFARIN tablet"


def test_europe_pmc_client_normalizes_article_metadata(monkeypatch, tmp_path):
    def fake_request(method, url, timeout=None, **kwargs):
        assert method == "GET"
        assert "query" in kwargs["params"]
        return FakeResponse(
            200,
            {
                "resultList": {
                    "result": [
                        {
                            "title": "Warfarin fluconazole interaction",
                            "pubYear": "2024",
                            "journalTitle": "Clinical Pharmacology",
                            "pmid": "123",
                            "doi": "10.1/example",
                        }
                    ]
                }
            },
        )

    client = EuropePMCClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client._session, "request", fake_request)

    payload = client.search_interaction_literature("warfarin", "fluconazole")

    assert payload["found"] is True
    assert payload["articles"][0]["pmid"] == "123"
    assert payload["articles"][0]["url"].endswith("/123")


def test_stringdb_client_maps_and_normalizes_network(monkeypatch, tmp_path):
    def fake_request(method, url, timeout=None, **kwargs):
        if url.endswith("/get_string_ids"):
            return FakeResponse(
                200,
                [
                    {
                        "queryItem": "CYP2C9",
                        "stringId": "9606.ENSP0001",
                        "preferredName": "CYP2C9",
                        "annotation": "Cytochrome P450",
                    }
                ],
            )
        if url.endswith("/network"):
            return FakeResponse(
                200,
                [
                    {
                        "preferredName_A": "CYP2C9",
                        "preferredName_B": "VKORC1",
                        "score": 0.81,
                    }
                ],
            )
        return FakeResponse(404, {})

    client = StringDBClient(cache_dir=str(tmp_path), caller_identity="infermed-test")
    monkeypatch.setattr(client._session, "request", fake_request)

    payload = client.get_network_summary(["CYP2C9"])

    assert payload["found"] is True
    assert payload["mapped"][0]["preferred_name"] == "CYP2C9"
    assert payload["interactions"][0]["protein_b"] == "VKORC1"


def test_drugcentral_client_normalizes_structure_and_targets(monkeypatch, tmp_path):
    def fake_request(method, url, timeout=None, **kwargs):
        if "/structures/name/" in url:
            return FakeResponse(
                200,
                [
                    {
                        "id": 2847,
                        "cd_id": 1758,
                        "name": "warfarin",
                        "cas_reg_no": "81-81-2",
                        "cd_formula": "C19H16O4",
                        "cd_molweight": 308.333,
                        "smiles": "CC(=O)CC...",
                        "inchikey": "PJVWKTKQMONHTI-UHFFFAOYSA-N",
                        "mrdef": "An anticoagulant.",
                    }
                ],
            )
        if "/act_table_full/struct_id/2847" in url:
            return FakeResponse(
                200,
                [
                    {
                        "gene": "VKORC1",
                        "target_name": "Vitamin K epoxide reductase complex subunit 1",
                        "act_type": "IC50",
                        "act_value": 7.0,
                        "action_type": "INHIBITOR",
                        "target_class": "Enzyme",
                        "organism": "Homo sapiens",
                        "act_source": "WOMBAT-PK",
                    }
                ],
            )
        return FakeResponse(404, {})

    client = DrugCentralClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client._session, "request", fake_request)

    payload = client.get_drug_summary("warfarin")

    assert payload["found"] is True
    assert payload["structure"]["name"] == "warfarin"
    assert payload["targets"][0]["gene"] == "VKORC1"
    assert payload["targets"][0]["action_type"] == "INHIBITOR"


def test_open_targets_client_normalizes_search_hits(monkeypatch, tmp_path):
    def fake_request(method, url, timeout=None, **kwargs):
        assert method == "POST"
        return FakeResponse(
            200,
            {
                "data": {
                    "search": {
                        "hits": [
                            {
                                "id": "ENSG000001",
                                "name": "CYP2C9",
                                "entity": "target",
                                "description": "cytochrome P450 family 2 subfamily C member 9",
                            }
                        ]
                    }
                }
            },
        )

    client = OpenTargetsClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client._session, "request", fake_request)

    payload = client.search("CYP2C9")

    assert payload["found"] is True
    assert payload["hits"][0]["entity"] == "target"


def test_fda_pgx_client_matches_page_snippets(monkeypatch, tmp_path):
    def fake_get(url, timeout=None):
        return FakeResponse(200, {}, text="<html><body>Warfarin CYP2C9 VKORC1 labeling biomarker table.</body></html>")

    client = FDAPGxClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client._session, "get", fake_get)

    payload = client.get_pair_matches("warfarin", "fluconazole")

    assert payload["found"] is True
    assert "Warfarin" in payload["a"][0]["snippet"]


def test_biogrid_client_is_credential_gated(tmp_path):
    client = BioGRIDClient(access_key="", cache_dir=str(tmp_path))

    payload = client.get_interactions(["CYP2C9"])

    assert payload["available"] is False
    assert payload["interactions"] == []
