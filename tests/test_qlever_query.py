# tests/test_qlever_query.py
import os
import re
import pytest

from src.retrieval.qlever_query import (
    QLeverClient,
    # Constants we expect to exist
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
    # Public helpers
    core_find_cid_by_label_fragment,
    get_mechanistic,
)

import src.retrieval.qlever_query as ql

# --------------------------------------------------------------------------------------
# Helpers

CORE_ENDPOINT = os.getenv("CORE_ENDPOINT", "http://localhost:7010/")
BIO_ENDPOINT  = os.getenv("BIO_ENDPOINT",  "http://localhost:7012/")

def _ping(client: QLeverClient) -> bool:
    """Returns True if endpoint answers a trivial ASK."""
    try:
        res = client.query("ASK { ?s ?p ?o }")
        return bool(res.get("boolean", False))
    except Exception:
        return False

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
    assert OBI_0000299 == "http://purl.obolibrary.org/obo/OBI_0000299"           # has_specified_output (MG -> Endpoint)
    assert IAO_0000136 == "http://purl.obolibrary.org/obo/IAO_0000136"           # is_about (Endpoint -> SID)
    assert RO_0000056  == "http://purl.obolibrary.org/obo/RO_0000056"            # participates_in (SID -> MG)
    assert SIO_VALUE   == "http://semanticscience.org/resource/SIO_000300"       # numeric value
    assert SIO_UNIT    == "http://semanticscience.org/resource/SIO_000221"       # unit
    assert PCV_OUTCOME == "http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#PubChemAssayOutcome"
    assert RO_0000057  == "http://purl.obolibrary.org/obo/RO_0000057"            # has_participant (Endpoint -> Protein)
    assert MG_PREFIX   == "http://rdf.ncbi.nlm.nih.gov/pubchem/measuregroup/"
    assert EP_PREFIX   == "http://rdf.ncbi.nlm.nih.gov/pubchem/endpoint/"
    assert PUBCHEM_COMPOUND_NS.startswith("http://rdf.ncbi.nlm.nih.gov/pubchem/compound/")

# --------------------------------------------------------------------------------------
# CORE index tests
@pytest.mark.integration
def test_core_label_fragment_contains_aspirin(core_client):
    pairs = ql.core_find_cid_by_label_fragment("aspirin", limit=20)
    # Should return (cid_uri, name)
    assert isinstance(pairs, list)
    assert all(isinstance(t, tuple) and len(t) == 2 for t in pairs)
    assert all(isinstance(t, tuple) and len(t) == 2 for t in pairs)
    # Names should include aspirin fragment (case-insensitive)
    assert any("aspirin" in name.lower() for _, name in pairs)
    # URIs should be in the PubChem compound namespace
    assert all(cid.startswith(PUBCHEM_COMPOUND_NS) for cid, _ in pairs)

@pytest.mark.integration
def test_core_find_cid_by_label_fragment(core_client):
    # This helper uses _ensure_client('core') internally, so just call it.
    pairs = core_find_cid_by_label_fragment("ibuprofen", limit=25)
    assert isinstance(pairs, list)
    assert any("ibuprofen" in name.lower() for _, name in pairs)

# --------------------------------------------------------------------------------------
# Mechanistic stub schema

def test_get_mechanistic_schema_is_stable():
    mech = get_mechanistic("Aspirin", "Clopidogrel")
    # Top-level keys
    expected_keys = {
        "enzymes", "targets_a", "targets_b",
        "pathways_a", "pathways_b", "common_pathways",
        "ids_a", "ids_b", "synonyms_a", "synonyms_b", "caveats"
    }
    assert expected_keys.issubset(mech.keys())

    # Enzymes structure
    assert "a" in mech["enzymes"] and "b" in mech["enzymes"]
    for side in ("a", "b"):
        ez = mech["enzymes"][side]
        assert set(ez.keys()) == {"substrate", "inhibitor", "inducer"}
        assert all(isinstance(ez[k], list) for k in ez)

    # Types for IDs/synonyms/caveats
    assert isinstance(mech["ids_a"], dict) and isinstance(mech["ids_b"], dict)
    assert isinstance(mech["synonyms_a"], list) and isinstance(mech["synonyms_b"], list)
    assert isinstance(mech["caveats"], list) and len(mech["caveats"]) >= 1

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
    # 1) Grab one MG
    q_mg = f"""
    SELECT DISTINCT ?mg WHERE {{
      ?mg ?p ?o .
      FILTER(CONTAINS(STR(?mg), "/measuregroup/"))
    }} LIMIT 1
    """
    res_mg = bio_client.query(q_mg)
    mg_rows = res_mg.get("results", {}).get("bindings", [])
    if not mg_rows:
        pytest.skip("No /measuregroup/ resources in BIO index (did you load PubChem measuregroup TTLs?)")
    mg = mg_rows[0]["mg"]["value"]
    assert mg.startswith(MG_PREFIX)

    # 2) Endpoints connected to that MG
    q_ep = f"""
    PREFIX OBI:<{OBI_0000299}>
    SELECT ?e WHERE {{
      <{mg}> OBI:?x ?e .  # QLever allows prefixed IRI with variable after colon? -> Use explicit IRI instead.
    }}
    """
    # The above inline PREFIX-IRI-variable trick won't work. Use explicit triple with full IRI:
    q_ep = f"""
    SELECT ?e WHERE {{
      <{mg}> <{OBI_0000299}> ?e .
    }} LIMIT 50
    """
    res_ep = bio_client.query(q_ep)
    eps = [b["e"]["value"] for b in res_ep.get("results", {}).get("bindings", [])]
    assert eps, "MeasureGroup has no Endpoints via OBI:0000299"

    # 3) OPTIONAL value/unit/outcome for endpoints
    q_vals = f"""
    PREFIX sio:<{SIO_VALUE.rsplit('/', 1)[0] + '/'}>   # shorthand for sio:
    PREFIX pcv:<http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#>
    SELECT ?e ?val ?unit ?outcome WHERE {{
      <{mg}> <{OBI_0000299}> ?e .
      OPTIONAL {{ ?e <{SIO_VALUE}> ?val }}
      OPTIONAL {{ ?e <{SIO_UNIT}>  ?unit }}
      OPTIONAL {{ ?e <{PCV_OUTCOME}> ?outcome }}
    }} LIMIT 200
    """
    res_vals = bio_client.query(q_vals)
    rows_vals = res_vals.get("results", {}).get("bindings", [])
    assert rows_vals, "No endpoints (or none materialized with values)"
    # At least endpoints are proper IRIs
    assert all("e" in r and r["e"]["value"].startswith(EP_PREFIX) for r in rows_vals)

    # 4) SID -> CID join (via either MG or Endpoint) to ensure the path is available
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
    res_sc = bio_client.query(q_sid_cid)
    rows_sc = res_sc.get("results", {}).get("bindings", [])
    assert rows_sc, "Expected at least one SID→CID mapping for MG"
    assert all(r["cid"]["value"].startswith("http://rdf.ncbi.nlm.nih.gov/pubchem/compound/CID")
               for r in rows_sc)
