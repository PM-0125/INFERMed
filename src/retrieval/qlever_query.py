# src/retrieval/qlever_query.py
# -*- coding: utf-8 -*-
"""
QLever SPARQL retrieval for INFERMed (PubChem shards).

Shards (override with env vars):
- CORE (labels, synonyms, descriptors/SMILES, pathways):  QLEVER_ENDPOINT_CORE (default http://127.0.0.1:7001)
- BIO  (bioassays/endpoints → proteins/targets, metabolism heuristics): QLEVER_ENDPOINT_BIO (http://127.0.0.1:7002)
- DIS  (disease/phenotype; not used directly yet): QLEVER_ENDPOINT_DIS (http://127.0.0.1:7003)

Public API:
- resolve_drug_to_cid(drug_name) -> Optional[str]
- get_targets(drug_name) -> List[str]
- get_targets_by_smiles(drug_smiles) -> List[str]
- get_common_pathways(drug1, drug2) -> List[str]
- get_metabolism_profile(drug_name) -> Dict[str, Any]
- get_metabolism_profile_by_id(drug_id) -> Dict[str, Any]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import requests


# ---------- Config ----------

QLEVER_ENDPOINT_CORE = os.getenv("QLEVER_ENDPOINT_CORE", "http://127.0.0.1:7001")
QLEVER_ENDPOINT_BIO = os.getenv("QLEVER_ENDPOINT_BIO", "http://127.0.0.1:7002")
QLEVER_ENDPOINT_DIS = os.getenv("QLEVER_ENDPOINT_DIS", "http://127.0.0.1:7003")

# Namespaces used in filtered PubChemRDF slices
NS = {
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "sio": "http://semanticscience.org/resource/",
    "compound": "http://rdf.ncbi.nlm.nih.gov/pubchem/compound/",
    "descriptor": "http://rdf.ncbi.nlm.nih.gov/pubchem/descriptor/",
    "synonym": "http://rdf.ncbi.nlm.nih.gov/pubchem/synonym/",
    "protein": "http://rdf.ncbi.nlm.nih.gov/pubchem/protein/",
    "pathway": "http://rdf.ncbi.nlm.nih.gov/pubchem/pathway/",
    "endpoint": "http://rdf.ncbi.nlm.nih.gov/pubchem/endpoint/",
}

# Fixed predicates (PSO/POS-safe). Add more candidates if your slice differs.
PRED = {
    "label": f"<{NS['rdfs']}label>",
    "sio_about": f"<{NS['sio']}SIO_000011>",  # links (is about / refers to)
    "sio_value": f"<{NS['sio']}SIO_000300>",  # literal value on nodes
    "compound_to_descriptor": [
        f"<{NS['sio']}SIO_000011>",  # compound ↔ descriptor node
    ],
    "descriptor_to_literal": [
        f"<{NS['sio']}SIO_000300>",  # descriptor node → literal (e.g., canSMILES)
    ],
    "compound_to_pathway": [
        f"<{NS['sio']}SIO_000011>",  # pathway node ↔ compound
    ],
    "endpoint_to_compound": [
        f"<{NS['sio']}SIO_000011>",  # endpoint ↔ compound
    ],
    "endpoint_to_protein": [
        f"<{NS['sio']}SIO_000011>",  # endpoint ↔ protein
    ],
    "endpoint_to_outcome": [
        f"<{NS['sio']}SIO_000011>",  # endpoint ↔ outcome/evidence node
    ],
}

CACHE_DIR = os.getenv("QLEVER_CACHE_DIR")  # e.g., /mnt/data_vault/INFERMed_cache/qlever
DEFAULT_TIMEOUT = (5, 60)  # (connect, read) seconds
USER_AGENT = "INFERMed-QLever/1.0"


# ---------- Utilities ----------

def _sparql_literal(s: str) -> str:
    """Escape a Python string to a safe SPARQL double-quoted literal."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'

def _shorten_iri(iri: str) -> str:
    if not iri:
        return iri
    m = re.search(r"(CID\d+)$", iri)
    if m:
        return m.group(1)
    return iri.rstrip(">").split("/")[-1]

def _normalize_label(s: str) -> str:
    return s.strip()

def _cid_iri(cid_or_label: str) -> str:
    if cid_or_label.upper().startswith("CID"):
        numeric = cid_or_label[3:]
    else:
        numeric = cid_or_label
    numeric = re.sub(r"\D", "", numeric)
    return f"<{NS['compound']}CID{numeric}>"

def _hashkey(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()

def _disk_cache_get(key: str) -> Optional[Any]:
    if not CACHE_DIR:
        return None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, f"{key}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None

def _disk_cache_set(key: str, obj: Any) -> None:
    if not CACHE_DIR:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, f"{key}.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


# ---------- Low-level client ----------

class QLeverClient:
    def __init__(self, base_url: str, timeout: Tuple[int, int] = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/sparql-results+json",
            "User-Agent": USER_AGENT,
        })

    def select(self, sparql: str) -> List[Dict[str, Any]]:
        params = {"query": sparql}
        r = self.session.get(self.base_url + "/", params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "ERROR":
            raise RuntimeError(data.get("exception", "QLever error"))
        return data.get("results", {}).get("bindings", [])


# ---------- High-level facade ----------

@dataclass
class Target:
    id: str
    label: str
    type: Optional[str] = None


class QLeverPubChem:
    """Routes queries to shards and normalizes outputs."""
    def __init__(self,
                 core_url: str = QLEVER_ENDPOINT_CORE,
                 bio_url: str = QLEVER_ENDPOINT_BIO,
                 disease_url: str = QLEVER_ENDPOINT_DIS):
        self.core = QLeverClient(core_url)
        self.bio = QLeverClient(bio_url)
        self.dis = QLeverClient(disease_url)

    # ----- Resolution helpers -----

    @lru_cache(maxsize=4096)
    def resolve_drug_to_cid(self, drug_name: str) -> Optional[str]:
        """Resolve brand/generic/synonym to CID via rdfs:label and synonym nodes."""
        key = _hashkey("resolve", drug_name.lower())
        cached = _disk_cache_get(key)
        if cached is not None:
            return cached or None

        name = drug_name.strip()
        lit = _sparql_literal(name)
        sparql = f"""
PREFIX rdfs: <{NS['rdfs']}>
PREFIX sio:  <{NS['sio']}>
SELECT DISTINCT ?cid ?name WHERE {{
  {{ ?cid {PRED['label']} ?name .
     FILTER(LCASE(?name) = LCASE({lit})) }}
  UNION
  {{ ?syn {PRED['sio_value']} ?synValue .
     FILTER(LCASE(?synValue) = LCASE({lit})) .
     ?syn {PRED['sio_about']} ?cid . }}
}}
LIMIT 20
"""
        try:
            rows = self.core.select(sparql)
        except Exception:
            rows = []
        cands: List[str] = []
        for b in rows:
            iri = (b.get("cid") or {}).get("value")
            if iri:
                cands.append(_shorten_iri(iri))
        cid = cands[0] if cands else None
        _disk_cache_set(key, cid)
        return cid

    def _cid_from_input(self, drug: str) -> Optional[str]:
        if re.match(r"^CID\d+$", drug, flags=re.I):
            return drug.upper()
        return self.resolve_drug_to_cid(drug)

    # ----- Public API -----

    def get_targets(self, drug_name: str) -> List[Target]:
        """
        Heuristic: endpoints linked to the compound (bio shard) also link to proteins.
        endpoint --sio:000011--> CID
        endpoint --sio:000011--> protein
        protein   rdfs:label     ?pLabel
        """
        cid = self._cid_from_input(drug_name)
        if not cid:
            return []
        cid_iri = _cid_iri(cid)

        ep_to_cid_block = " UNION ".join([f"{{ ?ep {p} {cid_iri} . }}" for p in PRED["endpoint_to_compound"]])
        ep_to_prot_block = " UNION ".join([f"{{ ?ep {p} ?prot . }}" for p in PRED["endpoint_to_protein"]])

        sparql = f"""
PREFIX rdfs: <{NS['rdfs']}>
SELECT DISTINCT ?prot ?pLabel WHERE {{
  {ep_to_cid_block}
  {ep_to_prot_block}
  ?prot {PRED['label']} ?pLabel .
}}
LIMIT 300
"""
        try:
            rows = self.bio.select(sparql)
        except Exception:
            rows = []
        out: List[Target] = []
        for b in rows:
            prot_iri = (b.get("prot") or {}).get("value")
            p_label = (b.get("pLabel") or {}).get("value")
            if prot_iri and p_label:
                out.append(Target(id=_shorten_iri(prot_iri), label=_normalize_label(p_label)))
        return out

    def _cid_by_smiles(self, smiles: str) -> Optional[str]:
        lit = _sparql_literal(smiles.strip())
        desc_to_lit = " UNION ".join([f"{{ ?d {p} {lit} }}" for p in PRED["descriptor_to_literal"]])
        cid_union = " UNION ".join([f"{{ ?cid {p} ?d }}" for p in PRED["compound_to_descriptor"]])

        sparql = f"""
SELECT DISTINCT ?cid WHERE {{
  {desc_to_lit}
  {cid_union}
}}
LIMIT 20
"""
        try:
            rows = self.core.select(sparql)
        except Exception:
            rows = []
        for b in rows:
            iri = (b.get("cid") or {}).get("value")
            if iri:
                return _shorten_iri(iri)
        return None

    def get_targets_by_smiles(self, drug_smiles: str) -> List[Target]:
        cid = self._cid_by_smiles(drug_smiles)
        if not cid:
            return []
        return self.get_targets(cid)

    def get_common_pathways(self, drug1: str, drug2: str) -> List[str]:
        """
        Pathway node ?p links to each CID via fixed predicates, then read rdfs:label.
        """
        cid1 = self._cid_from_input(drug1)
        cid2 = self._cid_from_input(drug2)
        if not cid1 or not cid2:
            return []
        c1, c2 = _cid_iri(cid1), _cid_iri(cid2)
        comp_to_path = " UNION ".join([f"{{ ?p {p} %CID% . }}" for p in PRED["compound_to_pathway"]])
        block1 = comp_to_path.replace("%CID%", c1)
        block2 = comp_to_path.replace("%CID%", c2)

        sparql = f"""
PREFIX rdfs: <{NS['rdfs']}>
SELECT DISTINCT ?pLabel WHERE {{
  {block1}
  {block2}
  ?p {PRED['label']} ?pLabel .
}}
LIMIT 400
"""
        try:
            rows = self.core.select(sparql)
        except Exception:
            rows = []
        labels = sorted({_normalize_label((b.get("pLabel") or {}).get("value", "")) for b in rows if b.get("pLabel")})
        return labels

    def get_metabolism_profile(self, drug_name: str) -> Dict[str, Any]:
        cid = self._cid_from_input(drug_name)
        return self.get_metabolism_profile_by_id(cid) if cid else {"cyp": {}, "transporters": {}, "raw": []}

    def get_metabolism_profile_by_id(self, drug_id: str) -> Dict[str, Any]:
        """
        Build a rough metabolism profile from endpoint / outcome labels (bio shard).
        Looks for CYP450 + P-gp strings and infers role (substrate/inhibitor/inducer).
        """
        if not drug_id:
            return {"cyp": {}, "transporters": {}, "raw": []}
        cid_iri = _cid_iri(drug_id)

        ep_to_cid = " UNION ".join([f"{{ ?ep {p} {cid_iri} . }}" for p in PRED["endpoint_to_compound"]])
        ep_to_out = " UNION ".join([f"{{ ?ep {p} ?out . }}" for p in PRED["endpoint_to_outcome"]])

        sparql = f"""
PREFIX rdfs: <{NS['rdfs']}>
SELECT DISTINCT ?ep ?epLabel ?outLabel WHERE {{
  {ep_to_cid}
  ?ep {PRED['label']} ?epLabel .
  OPTIONAL {{
    {ep_to_out}
    ?out {PRED['label']} ?outLabel .
  }}
  FILTER (REGEX(LCASE(?epLabel), "cyp3a4|cyp2d6|cyp2c9|cyp2c19|cyp1a2|cyp2b6|abcb1|p-gp|p-glycoprotein"))
}}
LIMIT 2000
"""
        try:
            rows = self.bio.select(sparql)
        except Exception:
            rows = []

        profile: Dict[str, List[str]] = {}
        transporters: Dict[str, List[str]] = {}
        raw: List[Dict[str, str]] = []

        for b in rows:
            ep_label = (b.get("epLabel") or {}).get("value", "")
            out_label = (b.get("outLabel") or {}).get("value", "")
            text = f"{ep_label} {out_label}".lower()

            enzyme = None
            for tag in ("cyp3a4", "cyp2d6", "cyp2c9", "cyp2c19", "cyp1a2", "cyp2b6"):
                if tag in text:
                    enzyme = tag.upper()
                    break

            role = None
            if any(k in text for k in ("substrate", "metabolized by")):
                role = "substrate"
            elif any(k in text for k in ("inhibit", "inhibitor", "inhibition")):
                role = "inhibitor"
            elif any(k in text for k in ("induce", "inducer", "induction", "upregulat")):
                role = "inducer"

            if enzyme and role:
                profile.setdefault(enzyme, [])
                if role not in profile[enzyme]:
                    profile[enzyme].append(role)

            if any(k in text for k in ("abcb1", "p-gp", "p-glycoprotein")):
                t_key = "ABCB1 (P-gp)"
                t_role = "substrate" if "substrate" in text else "inhibitor" if "inhib" in text else "inducer" if "induc" in text else None
                if t_role:
                    transporters.setdefault(t_key, [])
                    if t_role not in transporters[t_key]:
                        transporters[t_key].append(t_role)

            raw.append({"endpoint": ep_label, "outcome": out_label})

        return {"cyp": profile, "transporters": transporters, "raw": raw}


# ---------- Module-level API (what tests import) ----------

_client_singleton: Optional[QLeverPubChem] = None

def _client() -> QLeverPubChem:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = QLeverPubChem()
    return _client_singleton


def resolve_drug_to_cid(drug_name: str) -> Optional[str]:
    return _client().resolve_drug_to_cid(drug_name)


def get_targets(drug_name: str) -> List[str]:
    return [t.label for t in _client().get_targets(drug_name)]


def get_targets_by_smiles(drug_smiles: str) -> List[str]:
    return [t.label for t in _client().get_targets_by_smiles(drug_smiles)]


def get_common_pathways(drug1: str, drug2: str) -> List[str]:
    return _client().get_common_pathways(drug1, drug2)


def get_metabolism_profile(drug_name: str) -> Dict[str, Any]:
    return _client().get_metabolism_profile(drug_name)


def get_metabolism_profile_by_id(drug_id: str) -> Dict[str, Any]:
    return _client().get_metabolism_profile_by_id(drug_id)


__all__ = [
    "resolve_drug_to_cid",
    "get_targets",
    "get_targets_by_smiles",
    "get_common_pathways",
    "get_metabolism_profile",
    "get_metabolism_profile_by_id",
    "QLeverPubChem",
]
