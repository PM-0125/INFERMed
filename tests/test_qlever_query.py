# tests/test_qlever_query.py
import os
import pytest

from src.retrieval.qlever_query import (
    CORE_ENDPOINT, DISEASE_ENDPOINT,
    core_find_cid_by_exact_label, core_find_cid_by_label_fragment,
    core_descriptors_for_cids, core_xlogp_threshold,
    disease_find_by_label_fragment, disease_crossrefs,
    PUBCHEM_COMPOUND_NS
)

# --- Skip logic if endpoints are not configured --------------------------------

pytestmark = pytest.mark.skipif(
    not CORE_ENDPOINT or not DISEASE_ENDPOINT,
    reason="CORE_ENDPOINT and/or DISEASE_ENDPOINT not set; start QLever servers and export env."
)

ASPIRIN = f"{PUBCHEM_COMPOUND_NS}CID2244"
PHENYLETHANOLAMINE = f"{PUBCHEM_COMPOUND_NS}CID1000"


# --- Core label lookups --------------------------------------------------------

def test_core_exact_label_aspirin():
    got = core_find_cid_by_exact_label("Aspirin")
    assert ASPIRIN in got, f"Expected {ASPIRIN} in exact-label results"


def test_core_label_fragment_contains_aspirin():
    pairs = core_find_cid_by_label_fragment("aspirin", limit=50)
    cids = {cid for cid, _ in pairs}
    assert ASPIRIN in cids, "Fragment search (with fallback) should return Aspirin CID"


# --- Core descriptors ----------------------------------------------------------

def test_core_descriptors_for_known_cids():
    data = core_descriptors_for_cids([ASPIRIN, PHENYLETHANOLAMINE])
    assert ASPIRIN in data and PHENYLETHANOLAMINE in data

    # Keys must be normalized (no 'CID####_' prefix)
    asp = data[ASPIRIN]
    assert "XLogP3" in asp
    assert "Canonical_SMILES" in asp
    # Values exist (not necessarily asserting exact numeric/string here)
    assert asp["XLogP3"] != ""


# --- XLogP threshold slice -----------------------------------------------------

def test_core_xlogp_threshold_slice_contains_examples():
    # Deterministic ORDER BY + follow-up inclusion ensures both show up.
    res = core_xlogp_threshold(2.0, limit=500)
    assert ASPIRIN in res, "XLogP slice should include Aspirin (1.2)"
    assert PHENYLETHANOLAMINE in res, "XLogP slice should include Phenylethanolamine (0.1)"
    assert res[PHENYLETHANOLAMINE] <= 2.0
    assert res[ASPIRIN] <= 2.0


# --- Disease lookups -----------------------------------------------------------

def test_disease_label_fragment_meningioma():
    hits = disease_find_by_label_fragment("meningioma", limit=200)
    dz_ids = {d for d, _ in hits}
    assert "http://rdf.ncbi.nlm.nih.gov/pubchem/disease/DZID8458" in dz_ids


def test_disease_crossrefs_include_mesh_for_meningioma():
    xrefs = disease_crossrefs("http://rdf.ncbi.nlm.nih.gov/pubchem/disease/DZID8458", limit=1000)
    assert any(x.startswith("http://id.nlm.nih.gov/mesh/") for x in xrefs), "Expected MeSH crossref present"
