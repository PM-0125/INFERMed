# tests/test_qlever_query.py
# -*- coding: utf-8 -*-
"""
Integration tests for src/retrieval/qlever_query.py against local QLever shards.

Run:
    QLEVER_ENDPOINT_CORE=http://127.0.0.1:7001 \
    QLEVER_ENDPOINT_BIO=http://127.0.0.1:7002 \
    QLEVER_ENDPOINT_DIS=http://127.0.0.1:7003 \
    PYTHONPATH=. pytest -q tests/test_qlever_query.py
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

import pytest
import requests

# Make "retrieval" importable (we add the src/ dir to sys.path)
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from retrieval.qlever_query import (
    resolve_drug_to_cid,
    get_targets,
    get_targets_by_smiles,
    get_common_pathways,
    get_metabolism_profile,
    get_metabolism_profile_by_id,
)

# Default to 127.0.0.1 to avoid localhost/IPv6 quirks
CORE = os.getenv("QLEVER_ENDPOINT_CORE", "http://127.0.0.1:7001")
BIO  = os.getenv("QLEVER_ENDPOINT_BIO",  "http://127.0.0.1:7002")
DIS  = os.getenv("QLEVER_ENDPOINT_DIS",  "http://127.0.0.1:7003")

HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "INFERMed-tests/1.0",
}

def _ping(endpoint: str, timeout=(3, 10)) -> bool:
    """Probe shard without predicate variables (PSO/POS-safe)."""
    q = "SELECT (1 AS ?x) WHERE { VALUES ?z {1} } LIMIT 1"
    try:
        r = requests.get(
            endpoint.rstrip("/") + "/",
            params={"query": q},
            timeout=timeout,
            headers=HEADERS,
        )
        if r.status_code != 200:
            return False
        js = r.json()
        # Consider it up if we see a results table and not an explicit error
        if js.get("status") == "ERROR":
            return False
        return "results" in js and "bindings" in js["results"]
    except Exception:
        return False

core_up = pytest.mark.skipif(not _ping(CORE), reason=f"Core QLever shard is not reachable on {CORE}")
bio_up  = pytest.mark.skipif(not _ping(BIO),  reason=f"Bioactivity QLever shard is not reachable on {BIO}")
dis_up  = pytest.mark.skipif(not _ping(DIS),  reason=f"Disease QLever shard is not reachable on {DIS}")

@core_up
def test_resolve_drug_to_cid_aspirin_case_insensitive():
    cid1 = resolve_drug_to_cid("aspirin")
    cid2 = resolve_drug_to_cid("ASPIRIN")
    assert cid1 and re.match(r"^CID\d+$", cid1), f"Bad CID for aspirin: {cid1}"
    assert cid2 == cid1, "Resolution should be case-insensitive"

@core_up
def test_resolve_drug_to_cid_synonym_same_cid():
    cid_syn = resolve_drug_to_cid("Acetylsalicylic acid")
    cid_ref = resolve_drug_to_cid("aspirin")
    assert cid_syn and cid_ref and cid_syn == cid_ref, "Synonym should resolve to same CID"

@bio_up
def test_get_targets_aspirin_contains_cox1_like_name():
    targets = [t.lower() for t in get_targets("aspirin")]
    acceptable = ["prostaglandin g/h synthase 1", "cyclooxygenase-1", "ptgs1"]
    assert any(any(a in t for a in acceptable) for t in targets), \
        f"Expected a COX-1/PTGS1-like target in {targets[:15]}"

@core_up
@bio_up
def test_get_targets_by_smiles_nonempty():
    smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"  # aspirin
    targets = get_targets_by_smiles(smiles)
    assert isinstance(targets, list)
    assert len(targets) > 0, "Expected at least one target via SMILES→CID→targets path"

@core_up
def test_common_pathways_warfarin_aspirin_platelet_related():
    pathways = [p.lower() for p in get_common_pathways("warfarin", "aspirin")]
    assert any("platelet" in p for p in pathways), f"No platelet-related pathway found in {pathways[:20]}"

@bio_up
def test_metabolism_profile_simvastatin_has_cyp3a4_role_or_xfail():
    prof = get_metabolism_profile("simvastatin")
    assert isinstance(prof, dict) and "cyp" in prof
    roles = prof["cyp"].get("CYP3A4", [])
    if not roles:
        pytest.xfail("CYP3A4 evidence not present in current bioactivity slice")
    else:
        assert any(r in ("substrate", "inhibitor", "inducer") for r in roles)

@bio_up
def test_metabolism_profile_by_id_accepts_cid_and_returns_shape():
    cid = resolve_drug_to_cid("aspirin")
    assert cid and cid.upper().startswith("CID")
    prof = get_metabolism_profile_by_id(cid)
    assert "cyp" in prof and "transporters" in prof and "raw" in prof
    assert isinstance(prof["raw"], list)
