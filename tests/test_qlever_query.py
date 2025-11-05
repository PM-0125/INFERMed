# tests/test_qlever_query.py
import os
import pytest

from src.retrieval.qlever_query import (
    QLeverClient,
    # constants
    OBI_0000299,
    IAO_0000136,
    RO_0000056,
    SIO_VALUE,
    SIO_UNIT,
    PCV_OUTCOME,
    RO_0000057,
    MG_PREFIX,
    EP_PREFIX,
    PUBCHEM_COMPOUND_NS,
)

import src.retrieval.qlever_query as ql  # for helpers and exceptions


# --------------------------------------------------------------------------------------
# Helpers

CORE_ENDPOINT = os.getenv("CORE_ENDPOINT", "http://localhost:7010/")
BIO_ENDPOINT  = os.getenv("BIO_ENDPOINT",  "http://localhost:7012/")

def _ping(client: QLeverClient) -> bool:
    try:
        res = client.query("ASK { ?s ?p ?o }")
        return bool(res.get("boolean"))
    except Exception:
        return False

def _skip_on_qlever_hiccup(fn, *args, **kwargs):
    """Run a call that might hit a transient QLever issue; skip on timeout/5xx/etc."""
    try:
        return fn(*args, **kwargs)
    except (ql.QLeverTimeout, ql.QLeverError) as e:
        pytest.skip(f"QLever transient/unavailable: {e}")


@pytest.fixture(scope="session")
def core_client():
    client = QLeverClient(endpoint=CORE_ENDPOINT)
    if not _ping(client):
        pytest.skip(f"CORE endpoint not reachable at {CORE_ENDPOINT}")
    return client

@pytest.fixture(scope="session")
def bio_client():
    client = QLeverClient(endpoint=BIO_ENDPOINT)
    if not _ping(client):
        pytest.skip(f"BIO endpoint not reachable at {BIO_ENDPOINT}")
    return client


# --------------------------------------------------------------------------------------
# Constants

def test_constants_present_and_correct():
    assert OBI_0000299 == "http://purl.obolibrary.org/obo/OBI_0000299"
    assert IAO_0000136 == "http://purl.obolibrary.org/obo/IAO_0000136"
    assert RO_0000056  == "http://purl.obolibrary.org/obo/RO_0000056"
    assert SIO_VALUE   == "http://semanticscience.org/resource/SIO_000300"
    assert SIO_UNIT    == "http://semanticscience.org/resource/SIO_000221"
    assert PCV_OUTCOME == "http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#PubChemAssayOutcome"
    assert RO_0000057  == "http://purl.obolibrary.org/obo/RO_0000057"
    assert MG_PREFIX   == "http://rdf.ncbi.nlm.nih.gov/pubchem/measuregroup/"
    assert EP_PREFIX   == "http://rdf.ncbi.nlm.nih.gov/pubchem/endpoint/"
    assert PUBCHEM_COMPOUND_NS.startswith("http://rdf.ncbi.nlm.nih.gov/pubchem/compound/")


# --------------------------------------------------------------------------------------
# CORE index tests

@pytest.mark.integration
def test_core_label_fragment_contains_aspirin(core_client):
    pairs = _skip_on_qlever_hiccup(
        ql.core_find_cid_by_label_fragment, "aspirin", 20
    )
    assert isinstance(pairs, list)
    assert any("aspirin" in name.lower() for _, name in pairs)
    assert all(cid.startswith(PUBCHEM_COMPOUND_NS) for cid, _ in pairs)

@pytest.mark.integration
def test_core_find_cid_by_label_fragment(core_client):
    pairs = _skip_on_qlever_hiccup(
        ql.core_find_cid_by_label_fragment, "ibuprofen", 25
    )
    assert isinstance(pairs, list)
    assert any("ibuprofen" in name.lower() for _, name in pairs)


# --------------------------------------------------------------------------------------
# BIO index tests (light round-trip, auto-skip if MGs absent)

@pytest.mark.integration
def test_bio_measuregroup_roundtrip_smoke(bio_client):
    """
    1) Find one /measuregroup/ resource
    2) Fetch its endpoints via OBI:0000299
    3) From endpoints, try to pull value/unit/outcome (OPTIONALs)
    4) From MG/Endpoints, find SIDs -> CIDs
    """
    # 1) one MG
    q_mg = """
    SELECT DISTINCT ?mg WHERE {
      ?mg ?p ?o .
      FILTER(CONTAINS(STR(?mg), "/measuregroup/"))
    } LIMIT 1
    """
    res_mg = _skip_on_qlever_hiccup(bio_client.query, q_mg)
    mg_rows = res_mg.get("results", {}).get("bindings", [])
    if not mg_rows:
        pytest.skip("No /measuregroup/ resources in BIO index (load PubChem measuregroup TTLs).")
    mg = mg_rows[0]["mg"]["value"]
    assert mg.startswith(MG_PREFIX)

    # 2) Endpoints under that MG
    q_ep = f"""
    SELECT ?e WHERE {{
      <{mg}> <{OBI_0000299}> ?e .
    }} LIMIT 50
    """
    res_ep = _skip_on_qlever_hiccup(bio_client.query, q_ep)
    eps = [b["e"]["value"] for b in res_ep.get("results", {}).get("bindings", [])]
    assert eps, "MeasureGroup has no Endpoints via OBI:0000299"

    # 3) OPTIONAL value/unit/outcome
    q_vals = f"""
    SELECT ?e ?val ?unit ?outcome WHERE {{
      <{mg}> <{OBI_0000299}> ?e .
      OPTIONAL {{ ?e <{SIO_VALUE}> ?val }}
      OPTIONAL {{ ?e <{SIO_UNIT}>  ?unit }}
      OPTIONAL {{ ?e <{PCV_OUTCOME}> ?outcome }}
    }} LIMIT 200
    """
    res_vals = _skip_on_qlever_hiccup(bio_client.query, q_vals)
    rows_vals = res_vals.get("results", {}).get("bindings", [])
    assert rows_vals, "No endpoints (or none materialized with values)"
    assert all("e" in r and r["e"]["value"].startswith(EP_PREFIX) for r in rows_vals)

    # 4) SID -> CID join
    q_sid_cid = f"""
    PREFIX IAO:<{IAO_0000136}>
    SELECT DISTINCT ?sid ?cid WHERE {{
      <{mg}> <{OBI_0000299}> ?e .
      {{
        ?sid <{RO_0000056}> <{mg}> .
      }} UNION {{
        ?e IAO:IAO_0000136 ?sid .
      }}
      ?sid <http://semanticscience.org/resource/CHEMINF_000477> ?cid .
    }} LIMIT 200
    """
    res_sc = _skip_on_qlever_hiccup(bio_client.query, q_sid_cid)
    rows_sc = res_sc.get("results", {}).get("bindings", [])
    assert rows_sc, "Expected at least one SID→CID mapping for MG"
    assert all(r["cid"]["value"].startswith(PUBCHEM_COMPOUND_NS + "CID")
               for r in rows_sc)
