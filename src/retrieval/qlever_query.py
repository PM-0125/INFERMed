# src/retrieval/qlever_query.py
# -*- coding: utf-8 -*-
"""
Thin QLever client + helpers for PubChem CORE / DISEASE (+ optional BIO) endpoints.

Env:
  CORE_ENDPOINT=<qlever sparql endpoint url for core>      # required
  DISEASE_ENDPOINT=<qlever sparql endpoint url for disease># required (some funcs)
  BIO_ENDPOINT=<qlever sparql endpoint url for bio>        # optional but recommended

Optional tuning:
  QLEVER_MAX_RETRIES=2
  QLEVER_RETRY_BACKOFF=0.5
  QLEVER_RETRY_JITTER=0.2
  QLEVER_RETRY_5XX=1
  QLEVER_TIMEOUT_CORE=90
  QLEVER_TIMEOUT_DISEASE=90
  QLEVER_TIMEOUT_BIO=90  # Increased to 90s for more robust queries
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, cast

import requests

# ---------------------------------------------------------------------------
# Logging + endpoints
LOG = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("QLEVER_CLIENT_LOGLEVEL", "WARNING").upper())

CORE_ENDPOINT = os.getenv("CORE_ENDPOINT", "").rstrip("/") + ("/" if os.getenv("CORE_ENDPOINT") else "")
DISEASE_ENDPOINT = os.getenv("DISEASE_ENDPOINT", "").rstrip("/") + ("/" if os.getenv("DISEASE_ENDPOINT") else "")
BIO_ENDPOINT = os.getenv("BIO_ENDPOINT", "").rstrip("/") + ("/" if os.getenv("BIO_ENDPOINT") else "")

# ---------------------------------------------------------------------------
# Constants
PUBCHEM_COMPOUND_NS = "http://rdf.ncbi.nlm.nih.gov/pubchem/compound/"
SIO = "http://semanticscience.org/resource/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OBI_0000299 = "http://purl.obolibrary.org/obo/OBI_0000299"   # has_specified_output (MG -> Endpoint)
IAO_0000136 = "http://purl.obolibrary.org/obo/IAO_0000136"   # is_about (Endpoint -> SID)
RO_0000056  = "http://purl.obolibrary.org/obo/RO_0000056"    # participates_in (SID -> MG)
SIO_VALUE   = "http://semanticscience.org/resource/SIO_000300"
SIO_UNIT    = "http://semanticscience.org/resource/SIO_000221"
PCV_OUTCOME = "http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#PubChemAssayOutcome"
RO_0000057  = "http://purl.obolibrary.org/obo/RO_0000057"    # has_participant (Endpoint -> Protein/Gene)

MG_PREFIX = "http://rdf.ncbi.nlm.nih.gov/pubchem/measuregroup/"
EP_PREFIX = "http://rdf.ncbi.nlm.nih.gov/pubchem/endpoint/"

# ---------------------------------------------------------------------------
# Errors
class QLeverError(RuntimeError):
    pass

class QLeverTimeout(QLeverError):
    """Server 429 or client read/connect timeout."""

# ---------------------------------------------------------------------------
# Client
class QLeverClient:
    def __init__(self, endpoint: str, timeout_s: int = 30, session: Optional[requests.Session] = None):
        if not endpoint:
            raise ValueError("QLever endpoint is empty.")
        self.endpoint = endpoint.rstrip("/") + "/"
        self.timeout_s = timeout_s
        self.sess = session or requests.Session()
        self._headers = {"Accept": "application/sparql-results+json"}

        # env-configured retry defaults
        self.max_retries: int = int(os.getenv("QLEVER_MAX_RETRIES", "2"))
        self.retry_backoff: float = float(os.getenv("QLEVER_RETRY_BACKOFF", "0.5"))
        self.retry_jitter: float = float(os.getenv("QLEVER_RETRY_JITTER", "0.2"))
        self.retry_5xx: bool = os.getenv("QLEVER_RETRY_5XX", "1").lower() in {"1", "true", "yes"}

    def _calc_sleep(self, base: float, attempt: int) -> float:
        sleep = min(30.0, base * (2 ** attempt))
        if self.retry_jitter > 0:
            sleep += random.random() * self.retry_jitter
        return sleep

    def query(self, sparql: str, retries: Optional[int] = None, backoff_s: Optional[float] = None) -> dict:
        retries = self.max_retries if retries is None else retries
        backoff_s = self.retry_backoff if backoff_s is None else backoff_s

        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            resp: Optional[requests.Response] = None
            try:
                resp = self.sess.get(
                    self.endpoint,
                    params={"query": sparql},
                    headers=self._headers,
                    timeout=self.timeout_s,
                )
                status = resp.status_code

                # transient statuses
                if status == 429 or (500 <= status < 600 and self.retry_5xx):
                    if attempt < retries:
                        retry_after = 0.0
                        ra = resp.headers.get("Retry-After")
                        if ra:
                            try:
                                retry_after = float(int(ra))
                            except Exception:
                                pass
                        time.sleep(max(retry_after, self._calc_sleep(backoff_s, attempt)))
                        continue
                    if status == 429:
                        raise QLeverTimeout(self._extract_server_error(resp))
                    body = ""
                    try: body = resp.text[:2000]
                    except Exception: pass
                    raise QLeverError(f"HTTP {status} from {self.endpoint}: {body}")

                if not resp.ok:
                    body = ""
                    try: body = resp.text[:2000]
                    except Exception: pass
                    raise QLeverError(f"HTTP {status} from {self.endpoint}: {body}")

                return resp.json()

            except (requests.ReadTimeout, requests.ConnectTimeout) as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(self._calc_sleep(backoff_s, attempt)); continue
                raise QLeverTimeout(f"Client timeout contacting {self.endpoint}: {e}") from e
            except requests.ConnectionError as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(self._calc_sleep(backoff_s, attempt)); continue
                raise QLeverError(f"Connection error contacting {self.endpoint}: {e}") from e
            except requests.RequestException as e:
                last_exc = e
                status = getattr(resp, "status_code", "?")
                body = ""
                try:
                    if resp is not None:
                        body = resp.text[:2000]
                except Exception:
                    pass
                raise QLeverError(f"HTTP {status} from {self.endpoint}: {body}") from e

        raise QLeverError(f"Unreachable; last exception: {last_exc}")

    @staticmethod
    def _extract_server_error(r: requests.Response) -> str:
        try:
            j = r.json()
            if isinstance(j, dict):
                for k in ("exception", "error", "message"):
                    if k in j:
                        return f"429 from QLever: {j[k]}"
            return f"429 from QLever: {j}"
        except Exception:
            try:
                return f"429 from QLever (text): {r.text[:2000]}"
            except Exception:
                return "429 from QLever (no body)"

# ---------------------------------------------------------------------------
# Utilities

def _vals(bindings: Sequence[Dict[str, Any]], *cols: str) -> List[Tuple[str, ...]]:
    out: List[Tuple[str, ...]] = []
    for b in bindings:
        row: List[str] = []
        for c in cols:
            cell = b.get(c)
            if not cell:
                break
            row.append(cell["value"])
        else:
            out.append(tuple(row))
    return out

def _normalize_attr_key(raw_key: str) -> str:
    return re.sub(r"^CID\d+_", "", raw_key)

def _ensure_client(which: str) -> QLeverClient:
    if which == "core":
        if not CORE_ENDPOINT:
            raise QLeverError("CORE_ENDPOINT is not set in your environment.")
        return QLeverClient(CORE_ENDPOINT, timeout_s=int(os.getenv("QLEVER_TIMEOUT_CORE", "90")))
    elif which == "disease":
        if not DISEASE_ENDPOINT:
            raise QLeverError("DISEASE_ENDPOINT is not set in your environment.")
        return QLeverClient(DISEASE_ENDPOINT, timeout_s=int(os.getenv("QLEVER_TIMEOUT_DISEASE", "90")))
    raise AssertionError("Unknown client requested")

def get_clients_from_env() -> Tuple[QLeverClient, QLeverClient]:
    return _ensure_client("core"), _ensure_client("disease")

def sparql_str(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{s}\""

def _get_bio_endpoint() -> Optional[str]:
    return BIO_ENDPOINT or None

def _bio_query(query: str) -> Dict[str, Any]:
    endpoint = _get_bio_endpoint()
    if not endpoint or requests is None:
        return {}
    # Increased timeout for BIO queries - user prefers correctness over speed
    timeout = int(os.getenv("QLEVER_TIMEOUT_BIO", "90"))
    try:
        r = requests.get(
            endpoint,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except requests.Timeout:
        LOG.warning("BIO query timed out after %s seconds", timeout)
        return {}
    except Exception as e:
        LOG.debug("BIO query failed: %s", e)
        return {}

# ---------------------------------------------------------------------------
# CORE helpers (cached)

@lru_cache(maxsize=2048)
def core_find_cid_by_exact_label(label: str, limit: int = 50) -> List[str]:
    cli = _ensure_client("core")
    q = f"""
PREFIX skos:<{SKOS}>
SELECT ?cid WHERE {{
  ?cid skos:prefLabel {sparql_str(label)} .
  FILTER(STRSTARTS(STR(?cid), "{PUBCHEM_COMPOUND_NS}"))
}} LIMIT {int(limit)}
"""
    js = cli.query(q)
    return [cid for (cid,) in _vals(js["results"]["bindings"], "cid")]

@lru_cache(maxsize=2048)
def core_find_cid_by_label_fragment(fragment: str, limit: int = 50) -> List[Tuple[str, str]]:
    cli = _ensure_client("core")
    frag = fragment.strip()
    q = f"""
PREFIX skos:<{SKOS}>
SELECT ?cid ?name WHERE {{
  ?cid skos:prefLabel ?name .
  FILTER(STRSTARTS(STR(?cid), "{PUBCHEM_COMPOUND_NS}"))
  FILTER(CONTAINS(LCASE(STR(?name)), {sparql_str(frag.lower())}))
}} LIMIT {int(limit)}
"""
    try:
        js = cli.query(q, retries=0)
        return cast(List[Tuple[str, str]], _vals(js["results"]["bindings"], "cid", "name"))
    except QLeverTimeout:
        LOG.warning("Fragment query timed out; falling back to exact label variants for %r", frag)

    candidates = [frag, frag.capitalize(), frag.title(), frag.upper()]
    seen: set[str] = set()
    out: List[Tuple[str, str]] = []
    for c in candidates:
        for cid in core_find_cid_by_exact_label(c, limit=limit):
            if cid not in seen:
                out.append((cid, c))
                seen.add(cid)
    return out

@lru_cache(maxsize=4096)
def core_synonyms_for_cid(cid_uri: str, limit: int = 1024) -> List[str]:
    """
    Preferred + alt + rdfs labels as synonyms (primary).
    Fallback: labels from synonym nodes attached to the compound.
    """
    cli = _ensure_client("core")

    q1 = f"""
PREFIX skos:<{SKOS}>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?name WHERE {{
  VALUES ?c {{ <{cid_uri}> }}
  {{ ?c skos:prefLabel ?name }} UNION
  {{ ?c skos:altLabel ?name }} UNION
  {{ ?c rdfs:label ?name }}
}} LIMIT {int(limit)}
"""
    names: List[str] = []
    try:
        js = cli.query(q1)
        names = [n for (n,) in _vals(js["results"]["bindings"], "name")]
    except Exception:
        names = []

    if not names:
        q2 = f"""
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?name WHERE {{
  <{cid_uri}> ?p ?syn .
  ?syn rdfs:label ?name .
}} LIMIT {int(limit)}
"""
        try:
            js2 = cli.query(q2)
            names = [n for (n,) in _vals(js2["results"]["bindings"], "name")]
        except Exception:
            pass

    seen, out = set(), []
    for n in names:
        s = re.sub(r"\s+", " ", (n or "").strip())
        if s and s not in seen:
            out.append(s); seen.add(s)
    return out

def core_descriptors_for_cids(cids: Iterable[str]) -> Dict[str, Dict[str, str]]:
    cids = list(dict.fromkeys(cids))
    if not cids:
        return {}

    cli = _ensure_client("core")
    values = " ".join(f"<{cid}>" for cid in cids)
    q = f"""
PREFIX sio:<{SIO}>
SELECT ?cid ?key ?val WHERE {{
  VALUES ?cid {{ {values} }}
  ?cid sio:SIO_000008 ?attr .
  ?attr sio:SIO_000300 ?val .
  BIND(REPLACE(STR(?attr), ".*/", "") AS ?key)
  FILTER(REGEX(?key, "_(Canonical_SMILES|Isomeric_SMILES|IUPAC_InChI|Molecular_Formula|Molecular_Weight|Exact_Mass|TPSA|Hydrogen_Bond_Acceptor_Count|Hydrogen_Bond_Donor_Count|Rotatable_Bond_Count|XLogP3)$"))
}}
ORDER BY ?cid ?key
"""
    js = cli.query(q)
    out: Dict[str, Dict[str, str]] = {}
    for cid, raw_key, val in _vals(js["results"]["bindings"], "cid", "key", "val"):
        out.setdefault(cid, {})[_normalize_attr_key(raw_key)] = val
    return out

def _core_get_single_descriptor_value(cid: str, short_key: str) -> Optional[str]:
    cli = _ensure_client("core")
    q = f"""
PREFIX sio:<{SIO}>
SELECT ?val WHERE {{
  <{cid}> sio:SIO_000008 ?attr .
  FILTER(REGEX(STR(?attr), "_{short_key}$"))
  ?attr sio:SIO_000300 ?val .
}}
LIMIT 1
"""
    js = cli.query(q)
    vals = _vals(js["results"]["bindings"], "val")
    return vals[0][0] if vals else None

def core_xlogp_threshold(
    max_xlogp: float,
    limit: int = 1000,
    must_include_cids: Optional[List[str]] = None
) -> Dict[str, float]:
    cli = _ensure_client("core")
    q = f"""
PREFIX xsd:<http://www.w3.org/2001/XMLSchema#>
PREFIX sio:<{SIO}>
SELECT ?cid ?xlogp WHERE {{
  ?cid sio:SIO_000008 ?attr .
  FILTER(STRSTARTS(STR(?cid), "{PUBCHEM_COMPOUND_NS}"))
  FILTER(REGEX(STR(?attr), "_XLogP3$"))
  ?attr sio:SIO_000300 ?xlogp .
  FILTER(xsd:decimal(?xlogp) <= {float(max_xlogp):.6g})
}}
ORDER BY ?xlogp ?cid
LIMIT {int(limit)}
"""
    try:
        js = cli.query(q)
        results: Dict[str, float] = {}
        for cid, x in _vals(js["results"]["bindings"], "cid", "xlogp"):
            try:
                results[cid] = float(x)
            except ValueError:
                pass
        for cid in (must_include_cids or [f"{PUBCHEM_COMPOUND_NS}CID2244", f"{PUBCHEM_COMPOUND_NS}CID1000"]):
            if cid not in results:
                v = _core_get_single_descriptor_value(cid, "XLogP3")
                if v is not None:
                    try:
                        fv = float(v)
                        if fv <= max_xlogp:
                            results[cid] = fv
                    except ValueError:
                        pass
        return results
    except QLeverTimeout as e:
        LOG.warning("Global XLogP slice timed out; using per-CID fallback: %s", e)
        fallback_cids = must_include_cids or [
            f"{PUBCHEM_COMPOUND_NS}CID2244",
            f"{PUBCHEM_COMPOUND_NS}CID1000",
        ]
        results: Dict[str, float] = {}
        for cid in fallback_cids:
            v = _core_get_single_descriptor_value(cid, "XLogP3")
            if v is None: continue
            try:
                fv = float(v)
            except ValueError:
                continue
            if fv <= max_xlogp:
                results[cid] = fv
        return results

# ---------------------------------------------------------------------------
# DISEASE helpers

def disease_find_by_label_fragment(fragment: str, limit: int = 50) -> List[Tuple[str, str]]:
    cli = _ensure_client("disease")
    frag = fragment.strip().lower()
    q = f"""
PREFIX rdfs:<{RDFS}>
PREFIX skos:<{SKOS}>
SELECT ?d ?label WHERE {{
  ?d ?lp ?label .
  VALUES ?lp {{ rdfs:label skos:prefLabel skos:altLabel }}
  FILTER(CONTAINS(LCASE(STR(?label)), {sparql_str(frag)}))
}}
LIMIT {int(limit)}
"""
    js = cli.query(q)
    return cast(List[Tuple[str, str]], _vals(js["results"]["bindings"], "d", "label"))

def disease_crossrefs(dz_uri: str, limit: int = 1000) -> List[str]:
    cli = _ensure_client("disease")
    q = f"""
PREFIX skos:<{SKOS}>
SELECT ?ext WHERE {{
  <{dz_uri}> (skos:closeMatch|skos:relatedMatch|skos:exactMatch) ?ext
}}
LIMIT {int(limit)}
"""
    js = cli.query(q)
    return [ext for (ext,) in _vals(js["results"]["bindings"], "ext")]

@lru_cache(maxsize=4096)
def _query_diseases_for_cid(cid_uri: str, limit: int = 20) -> List[str]:
    """
    Query DISEASE index for diseases associated with a CID.
    
    Uses multiple small queries to find diseases through various relationships.
    Returns list of disease labels.
    """
    if not cid_uri:
        return []
    
    try:
        cli = _ensure_client("disease")
    except Exception as e:
        LOG.debug("DISEASE endpoint not available: %s", e)
        return []
    
    # Extract CID number from URI
    cid_match = re.search(r"CID(\d+)", cid_uri)
    if not cid_match:
        return []
    cid_num = cid_match.group(1)
    cid_uri_full = cid_uri
    
    # Multiple small queries to find diseases - optimized for completeness
    queries = [
        # Pattern 1: Direct compound-disease link via vocab:compound
        f"""
PREFIX rdfs:<{RDFS}>
PREFIX skos:<{SKOS}>
PREFIX vocab:<http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#>
SELECT DISTINCT ?disease ?label WHERE {{
  ?disease vocab:compound <{cid_uri_full}> .
  ?disease ?lp ?label .
  VALUES ?lp {{ rdfs:label skos:prefLabel skos:altLabel }}
}}
LIMIT {int(limit)}
""",
        # Pattern 2: Try alternative predicate patterns
        f"""
PREFIX rdfs:<{RDFS}>
PREFIX skos:<{SKOS}>
SELECT DISTINCT ?disease ?label WHERE {{
  ?disease ?pred <{cid_uri_full}> .
  FILTER(REGEX(STR(?pred), "(compound|drug|treatment)", "i"))
  ?disease ?lp ?label .
  VALUES ?lp {{ rdfs:label skos:prefLabel skos:altLabel }}
}}
LIMIT {int(limit)}
""",
        # Pattern 3: Reverse direction - compound has disease
        f"""
PREFIX rdfs:<{RDFS}>
PREFIX skos:<{SKOS}>
SELECT DISTINCT ?disease ?label WHERE {{
  <{cid_uri_full}> ?pred ?disease .
  FILTER(REGEX(STR(?pred), "(disease|indication|treatment)", "i"))
  ?disease ?lp ?label .
  VALUES ?lp {{ rdfs:label skos:prefLabel skos:altLabel }}
}}
LIMIT {int(limit)}
"""
    ]
    
    seen = set()
    diseases = []
    
    # Execute queries sequentially - small queries are faster
    for q in queries:
        try:
            js = cli.query(q)
            for b in js.get("results", {}).get("bindings", []):
                disease_uri = (b.get("disease", {}) or {}).get("value")
                label = (b.get("label", {}) or {}).get("value")
                
                if disease_uri and disease_uri not in seen:
                    seen.add(disease_uri)
                    if label:
                        diseases.append(label)
                    elif disease_uri:
                        # Extract disease ID from URI
                        dz_id = disease_uri.rsplit("/", 1)[-1]
                        if dz_id:
                            diseases.append(dz_id)
                    
                    if len(diseases) >= limit:
                        break
            if len(diseases) >= limit:
                break
        except Exception as e:
            LOG.debug("Disease query pattern failed for CID %s: %s", cid_uri, e)
            continue
    
    if not diseases:
        LOG.debug("No disease data found for CID %s - disease index may not have direct compound links", cid_uri)
    
    return _normalize_syns(diseases[:limit])

# ---------------------------------------------------------------------------
# BIO helpers

def bio_find_measuregroups_by_aid(aid: str, limit: int = 5) -> List[str]:
    aid = aid.strip()
    if not aid.startswith("AID"):
        aid = f"AID{aid}"
    q = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
SELECT DISTINCT ?mg WHERE {{
  ?mg OBI:OBI_0000299 ?e .
  FILTER(STRSTARTS(STR(?mg), "{MG_PREFIX}"))
  FILTER(STRSTARTS(STR(?e),  "{EP_PREFIX}"))
  FILTER(CONTAINS(STR(?e), "{aid}"))
}}
LIMIT {limit}
"""
    data = _bio_query(q)
    return [b["mg"]["value"] for b in data.get("results", {}).get("bindings", [])]

def bio_measuregroup_endpoints(mg_uri: str) -> List[Dict[str, Any]]:
    q = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
PREFIX sio:<http://semanticscience.org/resource/>
PREFIX pcv:<http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
SELECT ?e ?val ?unit ?unit_label ?outcome WHERE {{
  <{mg_uri}> OBI:OBI_0000299 ?e .
  OPTIONAL {{ ?e sio:SIO_000300 ?val }}
  OPTIONAL {{ ?e sio:SIO_000221 ?unit . OPTIONAL {{ ?unit rdfs:label ?unit_label }} }}
  OPTIONAL {{ ?e pcv:PubChemAssayOutcome ?outcome }}
}}
LIMIT 1000
"""
    data = _bio_query(q)
    out: List[Dict[str, Any]] = []
    for b in data.get("results", {}).get("bindings", []):
        out.append({
            "endpoint": b.get("e", {}).get("value"),
            "value": b.get("val", {}).get("value"),
            "unit": b.get("unit", {}).get("value"),
            "unit_label": b.get("unit_label", {}).get("value"),
            "outcome": b.get("outcome", {}).get("value"),
        })
    return out

def bio_measuregroup_sid_cid(mg_uri: str) -> List[Dict[str, str]]:
    q = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
PREFIX IAO:<http://purl.obolibrary.org/obo/>
PREFIX sio:<http://semanticscience.org/resource/>
SELECT DISTINCT ?sid ?cid WHERE {{
  <{mg_uri}> OBI:OBI_0000299 ?e .
  {{ ?sid <{RO_0000056}> <{mg_uri}> }} UNION {{ ?e <{IAO_0000136}> ?sid }} .
  ?sid <http://semanticscience.org/resource/CHEMINF_000477> ?cid .
}}
LIMIT 5000
"""
    data = _bio_query(q)
    return [{"sid": b["sid"]["value"], "cid": b["cid"]["value"]}
            for b in data.get("results", {}).get("bindings", [])]

def bio_measuregroup_proteins(mg_uri: str) -> List[Dict[str, Optional[str]]]:
    q = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?e ?prot ?prot_label WHERE {{
  <{mg_uri}> OBI:OBI_0000299 ?e .
  ?e <{RO_0000057}> ?prot .
  OPTIONAL {{ ?prot rdfs:label ?prot_label }}
}}
LIMIT 2000
"""
    data = _bio_query(q)
    return [{
        "endpoint": b.get("e", {}).get("value"),
        "protein": b.get("prot", {}).get("value"),
        "protein_label": b.get("prot_label", {}).get("value"),
    } for b in data.get("results", {}).get("bindings", [])]

def bio_measuregroup_endpoints_to_bioassay(mg_uri: str) -> List[Dict[str, str]]:
    q = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
SELECT ?e ?aidTok ?bioassay WHERE {{
  <{mg_uri}> OBI:OBI_0000299 ?e .
  BIND(REPLACE(STR(?e), ".*/.*_(AID[0-9]+)_.*", "$1") AS ?aidTok)
  BIND(IRI(CONCAT("http://rdf.ncbi.nlm.nih.gov/pubchem/bioassay/", ?aidTok)) AS ?bioassay)
}}
LIMIT 5000
"""
    data = _bio_query(q)
    return [{
        "endpoint": b.get("e", {}).get("value"),
        "aid": b.get("aidTok", {}).get("value"),
        "bioassay": b.get("bioassay", {}).get("value"),
    } for b in data.get("results", {}).get("bindings", [])]

def bio_measuregroup_summary(mg_uri: str) -> Dict[str, Any]:
    return {
        "measuregroup": mg_uri,
        "endpoints": bio_measuregroup_endpoints(mg_uri),
        "sid_cid": bio_measuregroup_sid_cid(mg_uri),
        "proteins": bio_measuregroup_proteins(mg_uri),
        "endpoint_to_bioassay": bio_measuregroup_endpoints_to_bioassay(mg_uri),
    }

# ---------------------------------------------------------------------------
# Mechanistic glue

def _extract_numeric_cid(cid_uri: str) -> Optional[str]:
    m = re.search(r"CID(\d+)", cid_uri or "")
    return m.group(1) if m else None

def _normalize_syns(syns: Iterable[str]) -> List[str]:
    out, seen = [], set()
    for s in syns:
        s = re.sub(r"\s+", " ", (s or "").strip())
        if s and s not in seen:
            out.append(s); seen.add(s)
    return out

def _first_cid_and_synonyms(name: str, limit: int = 25) -> tuple[Optional[str], Dict[str, Any]]:
    """
    Best-effort CID resolution + synonyms.
    Fast path: exact label probes (several casings), then fragment scan.
    Returns (cid_uri_or_none, info_dict={"ids":{"pubchem_cid":"<int>"}, "synonyms":[...]})
    """
    pairs: List[Tuple[str, str]] = []

    # 1) FAST: exact label (various casings)
    for s in {name, name.capitalize(), name.upper(), name.lower()}:
        try:
            cids = core_find_cid_by_exact_label(s)  # returns [cid_uri,...]
        except Exception:
            cids = []
        if cids:
            pairs = [(cid, s) for cid in cids]
            break

    # 2) Fallback: fragment
    if not pairs:
        try:
            pairs = core_find_cid_by_label_fragment(name, limit=limit)  # [(cid,label),...]
        except Exception:
            pairs = []

    if not pairs:
        return None, {"ids": {}, "synonyms": []}

    cid_uri, best_label = pairs[0]
    cid_num = _extract_numeric_cid(cid_uri)

    # Synonyms (SKOS/RDFS + fallback), ensure chosen label is first
    syns = core_synonyms_for_cid(cid_uri)
    if best_label and best_label not in syns:
        syns = [best_label] + syns

    info = {
        "ids": ({"pubchem_cid": cid_num} if cid_num else {}),
        "synonyms": _normalize_syns(syns)[:256],
    }
    return cid_uri, info

def _query_enzymes_for_cid(cid_uri: str) -> Dict[str, List[str]]:
    """
    Heuristic enzyme role extraction from PubChem RDF.
    NOTE: PubChem CORE index may not contain enzyme interaction data in the expected format.
    This function attempts multiple query patterns but may return empty results.
    Returns dict with 'substrate','inhibitor','inducer' lists (strings).
    """
    if not cid_uri:
        return {"substrate": [], "inhibitor": [], "inducer": []}

    cli = _ensure_client("core")
    enzymes = {"substrate": [], "inhibitor": [], "inducer": []}

    # Try multiple query patterns - PubChem RDF structure may vary
    queries = {
        "substrate": [
            # Pattern 1: Direct attribute-value pattern
            f"""
PREFIX sio:<{SIO}>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?enzyme ?label WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  ?attr sio:SIO_000300 ?enzyme .
  FILTER(REGEX(STR(?enzyme), "(?i)(cyp|cytochrome|p450)"))
  OPTIONAL {{ ?enzyme rdfs:label ?label }}
}}
LIMIT 50
""",
            # Pattern 2: Check if enzyme info is in attribute name
            f"""
PREFIX sio:<{SIO}>
SELECT DISTINCT ?attr WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  FILTER(REGEX(STR(?attr), "(?i)(cyp.*substrate|substrate.*cyp)"))
}}
LIMIT 20
"""
        ],
        "inhibitor": [
            f"""
PREFIX sio:<{SIO}>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?enzyme ?label WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  ?attr sio:SIO_000300 ?enzyme .
  FILTER(REGEX(STR(?attr), "(?i)(inhibit|inhibition)"))
  FILTER(REGEX(STR(?enzyme), "(?i)(cyp|cytochrome|p450)"))
  OPTIONAL {{ ?enzyme rdfs:label ?label }}
}}
LIMIT 50
""",
            f"""
PREFIX sio:<{SIO}>
SELECT DISTINCT ?attr WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  FILTER(REGEX(STR(?attr), "(?i)(cyp.*inhibit|inhibit.*cyp)"))
}}
LIMIT 20
"""
        ],
        "inducer": [
            f"""
PREFIX sio:<{SIO}>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?enzyme ?label WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  ?attr sio:SIO_000300 ?enzyme .
  FILTER(REGEX(STR(?attr), "(?i)(induc|induction)"))
  FILTER(REGEX(STR(?enzyme), "(?i)(cyp|cytochrome|p450)"))
  OPTIONAL {{ ?enzyme rdfs:label ?label }}
}}
LIMIT 50
""",
            f"""
PREFIX sio:<{SIO}>
SELECT DISTINCT ?attr WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  FILTER(REGEX(STR(?attr), "(?i)(cyp.*induc|induc.*cyp)"))
}}
LIMIT 20
"""
        ]
    }

    for role, query_list in queries.items():
        for query in query_list:
            try:
                js = cli.query(query, retries=0)  # Don't retry on timeout
                for b in js.get("results", {}).get("bindings", []):
                    enzyme = b.get("enzyme", {}).get("value", "")
                    label = b.get("label", {}).get("value", "")
                    attr = b.get("attr", {}).get("value", "")
                    
                    # Extract from label if available
                    if label:
                        enzymes[role].append(label)
                    # Extract from enzyme URI
                    elif enzyme:
                        m = re.search(r"(?i)(cyp\d+[a-z]?\d*)", enzyme)
                        if m:
                            enzymes[role].append(m.group(1).upper())
                    # Try to extract from attribute name
                    elif attr:
                        m = re.search(r"(?i)(cyp\d+[a-z]?\d*)", attr)
                        if m:
                            enzymes[role].append(m.group(1).upper())
            except (QLeverTimeout, QLeverError) as e:
                LOG.debug("Enzyme query timed out or failed for %s role: %s", role, e)
                continue
            except Exception as e:
                LOG.debug("Enzyme query error for %s role: %s", role, e)
                continue

    # Strategy 2: Query BIO index for CYP proteins (enzymes are proteins in bioactivity data)
    # Note: BIO index has CYP proteins but doesn't directly indicate role (substrate/inhibitor/inducer)
    # We'll extract all CYP proteins and treat them as potential substrates (most common role)
    if _get_bio_endpoint() and not any(enzymes.values()):  # Only if CORE didn't find anything
        try:
            bio_query = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms:<http://purl.org/dc/terms/>
SELECT DISTINCT ?prot ?prot_id ?label ?identifier WHERE {{
  {{
    ?mg OBI:OBI_0000299 ?e .
    ?e <{IAO_0000136}> ?sid .
    ?sid <http://semanticscience.org/resource/CHEMINF_000477> <{cid_uri}> .
    ?mg <{RO_0000057}> ?prot .
    FILTER(REGEX(STR(?prot), "(?i)(cyp|cytochrome)", "i"))
  }}
  UNION
  {{
    ?sub <http://semanticscience.org/resource/CHEMINF_000477> <{cid_uri}> .
    ?sub <http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#substance2measuregroup> ?mg .
    ?mg <{RO_0000057}> ?prot .
    FILTER(REGEX(STR(?prot), "(?i)(cyp|cytochrome)", "i"))
  }}
  BIND(REPLACE(STR(?prot), "http://rdf.ncbi.nlm.nih.gov/pubchem/protein/", "") AS ?prot_id)
  OPTIONAL {{ ?prot rdfs:label ?label }}
  OPTIONAL {{ ?prot dcterms:identifier ?identifier }}
}}
LIMIT 50
"""
            bio_data = _bio_query(bio_query)
            if bio_data:
                cyp_proteins = set()
                for b in bio_data.get("results", {}).get("bindings", []):
                    prot_id = (b.get("prot_id", {}) or {}).get("value", "")
                    label = (b.get("label", {}) or {}).get("value", "")
                    identifier = (b.get("identifier", {}) or {}).get("value", "")
                    
                    # Extract CYP identifier - try multiple patterns
                    cyp_name = None
                    if identifier:
                        m = re.search(r"(?i)cyp\s*(\d+[a-z]?\d*)", identifier, re.I)
                        if m:
                            cyp_name = f"CYP{m.group(1).upper()}"
                    elif label:
                        m = re.search(r"(?i)cyp\s*(\d+[a-z]?\d*)", label, re.I)
                        if m:
                            cyp_name = f"CYP{m.group(1).upper()}"
                    elif prot_id:
                        # Extract from protein ID (e.g., ACCYP_401673 -> CYP401673)
                        # Try to find CYP number pattern
                        m = re.search(r"(?i)cyp[_\s]?(\d+[a-z]?\d*)", prot_id, re.I)
                        if m:
                            num = m.group(1)
                            # Clean up: remove leading zeros for common CYPs (e.g., 401673 -> 401673, but 3A4 stays 3A4)
                            if len(num) > 3 and num.isdigit():
                                cyp_name = f"CYP{num}"
                            else:
                                cyp_name = f"CYP{num.upper()}"
                    
                    if cyp_name and cyp_name not in cyp_proteins:
                        cyp_proteins.add(cyp_name)
                        # Treat all CYP proteins as potential substrates (most common role)
                        # Note: BIO index doesn't distinguish substrate/inhibitor/inducer roles
                        enzymes["substrate"].append(cyp_name)
                
                if cyp_proteins:
                    LOG.debug("Found %d CYP enzymes in BIO index for CID %s", len(cyp_proteins), cid_uri)
        except Exception as e:
            LOG.debug("BIO enzyme query failed for CID %s: %s", cid_uri, e)
    
    # Deduplicate and normalize
    for role in enzymes:
        enzymes[role] = _normalize_syns(enzymes[role])
    
    if not any(enzymes.values()):
        LOG.debug("No enzyme data found for CID %s", cid_uri)
    
    return enzymes

@lru_cache(maxsize=4096)
def _query_targets_for_cid(cid_uri: str, limit: int = 32, return_dicts: bool = False) -> List[Any]:
    """
    Query BIO index for protein targets associated with a CID via MeasureGroups.
    
    Correct PubChem RDF structure:
      - CID -> SID (via CHEMINF_000477)
      - SID -> MeasureGroup (via substance2measuregroup)
      - MeasureGroup -> Protein (via RO_0000057 has_participant)
    
    Proteins are linked DIRECTLY to MeasureGroups, NOT to Endpoints.
    
    Args:
        cid_uri: Compound URI
        limit: Maximum number of targets to return
        return_dicts: If True, return list of dicts with 'uri' and 'label' keys
    
    Returns:
        If return_dicts=False: list of protein identifiers (strings)
        If return_dicts=True: list of dicts with 'uri' and 'label' keys
    """
    if not cid_uri or not _get_bio_endpoint():
        return []

    # Correct pattern: MeasureGroup -> RO_0000057 -> Protein (direct link)
    # Use simpler queries to avoid timeouts - split into steps
    queries = [
        # Pattern 1: Simple query via endpoint -> SID -> CID -> MG -> Protein
        f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
SELECT DISTINCT ?prot WHERE {{
  ?mg OBI:OBI_0000299 ?e .
  ?e <{IAO_0000136}> ?sid .
  ?sid <http://semanticscience.org/resource/CHEMINF_000477> <{cid_uri}> .
  ?mg <{RO_0000057}> ?prot .
  FILTER(STRSTARTS(STR(?prot), "http://rdf.ncbi.nlm.nih.gov/pubchem/protein/"))
}}
LIMIT {int(limit) * 2}
""",
        # Pattern 2: Via substance -> MeasureGroup -> Protein
        f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
SELECT DISTINCT ?prot WHERE {{
  ?sub <http://semanticscience.org/resource/CHEMINF_000477> <{cid_uri}> .
  ?sub <http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#substance2measuregroup> ?mg .
  ?mg <{RO_0000057}> ?prot .
  FILTER(STRSTARTS(STR(?prot), "http://rdf.ncbi.nlm.nih.gov/pubchem/protein/"))
}}
LIMIT {int(limit) * 2}
"""
    ]

    seen_prot_uris = set()
    prot_uris = []
    
    # Step 1: Get protein URIs (fast query)
    for q in queries:
        try:
            data = _bio_query(q)
            if not data:
                continue
            for b in data.get("results", {}).get("bindings", []):
                prot_uri = (b.get("prot", {}) or {}).get("value")
                if prot_uri and prot_uri not in seen_prot_uris:
                    seen_prot_uris.add(prot_uri)
                    prot_uris.append(prot_uri)
                    if len(prot_uris) >= limit * 2:
                        break
            if len(prot_uris) >= limit * 2:
                break
        except Exception as e:
            LOG.debug("BIO target query pattern failed for %s: %s", cid_uri, e)
            continue
    
    if not prot_uris:
        LOG.debug("No protein URIs found for CID %s", cid_uri)
        return []
    
    # Step 2: Get identifiers for proteins (batch query - but may timeout, so do in chunks)
    # If identifier query fails, fallback to extracting IDs from URIs
    targets = []
    chunk_size = 10  # Increased chunk size since timeout is now 30s
    
    # Try to get identifiers, but if it times out, use URI-based extraction
    identifier_query_succeeded = False
    
    for i in range(0, min(len(prot_uris), limit * 2), chunk_size):
        chunk = prot_uris[i:i+chunk_size]
        values = " ".join(f"<{p}>" for p in chunk)
        
        id_query = f"""
PREFIX rdf:<http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX dcterms:<http://purl.org/dc/terms/>
PREFIX rdfs:<{RDFS}>
SELECT ?prot ?prot_identifier ?uniprot_type ?prot_label WHERE {{
  VALUES ?prot {{ {values} }}
  OPTIONAL {{ ?prot dcterms:identifier ?prot_identifier }}
  OPTIONAL {{
    ?prot rdf:type ?type .
    FILTER(REGEX(STR(?type), "^http://purl.obolibrary.org/obo/PR_"))
    BIND(REPLACE(STR(?type), "http://purl.obolibrary.org/obo/PR_", "") AS ?uniprot_type)
  }}
  OPTIONAL {{ ?prot rdfs:label ?prot_label }}
}}
"""
        try:
            id_data = _bio_query(id_query)
            if id_data and id_data.get("results", {}).get("bindings"):
                identifier_query_succeeded = True
                for b in id_data.get("results", {}).get("bindings", []):
                    prot_uri = (b.get("prot", {}) or {}).get("value")
                    prot_identifier = (b.get("prot_identifier", {}) or {}).get("value")
                    uniprot_type = (b.get("uniprot_type", {}) or {}).get("value")
                    prot_label = (b.get("prot_label", {}) or {}).get("value")
                    
                    # Extract protein ID from URI
                    prot_id = prot_uri.rsplit("/", 1)[-1] if prot_uri else ""
                    
                    # Prefer: label > dcterms:identifier (UniProt) > rdf:type UniProt > cleaned protein ID
                    if return_dicts:
                        # Return dict with uri and label
                        label = prot_label or prot_identifier or uniprot_type or prot_id
                        if not label and prot_id:
                            # Clean up protein ID for label
                            clean_id = re.sub(r"^ACC[PQO]?", "", prot_id)
                            if re.match(r"^[PQO]\d+", prot_id):
                                label = f"UniProt:{prot_id}"
                            elif clean_id and len(clean_id) > 2:
                                label = clean_id
                            else:
                                label = prot_id
                        targets.append({"uri": prot_uri, "label": label or prot_id})
                    else:
                        # Return string identifier (backward compatible)
                        if prot_label:
                            targets.append(prot_label)
                        elif prot_identifier:
                            targets.append(f"UniProt:{prot_identifier}")
                        elif uniprot_type:
                            targets.append(f"UniProt:{uniprot_type}")
                        elif prot_id:
                            # Clean up protein ID
                            clean_id = re.sub(r"^ACC[PQO]?", "", prot_id)
                            if re.match(r"^[PQO]\d+", prot_id):
                                targets.append(f"UniProt:{prot_id}")
                            elif clean_id and len(clean_id) > 2:
                                targets.append(clean_id)
                            else:
                                targets.append(prot_id)
                    
                    if len(targets) >= limit:
                        break
                if len(targets) >= limit:
                    break
        except Exception as e:
            LOG.debug("BIO identifier query failed for chunk: %s", e)
            continue
    
    # Fallback: if identifier queries failed or returned no results, extract from URIs
    if not targets and prot_uris:
        LOG.debug("Using URI-based protein ID extraction (identifier query failed or timed out)")
        for prot_uri in prot_uris[:limit]:
            prot_id = prot_uri.rsplit("/", 1)[-1]
            # Clean up protein ID (remove ACC prefix, handle PDB-style IDs)
            clean_id = re.sub(r"^ACC", "", prot_id)
            # If it looks like a PDB chain ID (e.g., "1DE9_A"), keep as is
            if return_dicts:
                label = clean_id if re.match(r"^\d[A-Z0-9]{3}_[A-Z]$", clean_id) or (clean_id and len(clean_id) > 2) else prot_id
                targets.append({"uri": prot_uri, "label": label})
            else:
                if re.match(r"^\d[A-Z0-9]{3}_[A-Z]$", clean_id):
                    targets.append(clean_id)
                elif clean_id and len(clean_id) > 2:
                    targets.append(clean_id)
                elif prot_id:
                    targets.append(prot_id)
            if len(targets) >= limit:
                break
    
    if not targets:
        LOG.debug("No protein targets found for CID %s", cid_uri)
    
    # Don't normalize if returning dicts
    if return_dicts:
        return targets[:limit]
    return _normalize_syns(targets[:limit])

# ---------------------------------------------------------------------------
# Public: mechanistic bundles

def get_mechanistic(drugA: str, drugB: str) -> Dict[str, Any]:
    """
    Retrieve mechanistic PK/PD data from QLever indices.
    Returns dict:
      enzymes: {'a': {'substrate':[], 'inhibitor':[], 'inducer':[]}, 'b': {...}}
      targets_a / targets_b: [str, ...]
      diseases_a / diseases_b: [str, ...]  # NEW: diseases associated with compounds
      pathways_*: []  (reserved)
      ids_a / ids_b: {pubchem_cid: '...'}
      synonyms_a / synonyms_b: [str, ...]
      caveats: [str, ...]
    """
    caveats: List[str] = []

    try:
        _ = _ensure_client("core")
    except Exception as e:
        return {
            "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                        "b": {"substrate": [], "inhibitor": [], "inducer": []}},
            "targets_a": [], "targets_b": [],
            "diseases_a": [], "diseases_b": [],
            "pathways_a": [], "pathways_b": [], "common_pathways": [],
            "ids_a": {}, "ids_b": {},
            "synonyms_a": [], "synonyms_b": [],
            "caveats": [f"QLever CORE unavailable: {e}"],
        }

    # IDs & synonyms
    cid_a, a_info = _first_cid_and_synonyms(drugA)
    cid_b, b_info = _first_cid_and_synonyms(drugB)

    # Enzymes - try QLever first, then DrugBank fallback
    enzymes_a = {"substrate": [], "inhibitor": [], "inducer": []}
    enzymes_b = {"substrate": [], "inhibitor": [], "inducer": []}
    
    # Try QLever (CORE/BIO)
    if cid_a:
        try:
            enzymes_a = _query_enzymes_for_cid(cid_a)
            if not any(enzymes_a.values()):
                LOG.debug("No enzyme data from QLever for %s (CID %s)", drugA, cid_a)
        except Exception as e:
            LOG.warning("Enzyme query failed for %s: %s", drugA, e)
            caveats.append(f"Enzyme query failed for {drugA}: {e}")
    if cid_b:
        try:
            enzymes_b = _query_enzymes_for_cid(cid_b)
            if not any(enzymes_b.values()):
                LOG.debug("No enzyme data from QLever for %s (CID %s)", drugB, cid_b)
        except Exception as e:
            LOG.warning("Enzyme query failed for %s: %s", drugB, e)
            caveats.append(f"Enzyme query failed for {drugB}: {e}")
    
    # DrugBank fallback: if QLever didn't find enzymes, try DrugBank
    # ChEMBL enrichment: add potency and cross-validation
    # Import here to avoid circular dependency
    chembl_enabled = os.getenv("CHEMBL_ENABLED", "false").lower() == "true"
    chembl_data_a = None
    chembl_data_b = None
    
    try:
        from src.retrieval import duckdb_query as dq
        # Initialize DuckDB connection if needed
        dq.init_duckdb_connection("data/duckdb")
        db_client = dq.DuckDBClient("data/duckdb")
        
        # Check if QLever found any enzymes for drugA
        has_enzymes_a = any(enzymes_a.values())
        LOG.debug("DrugBank fallback check for %s: QLever found enzymes=%s", drugA, has_enzymes_a)
        if not has_enzymes_a:
            try:
                LOG.debug("Attempting DrugBank enzyme query for %s", drugA)
                db_enzymes_a = db_client.get_drug_enzymes(drugA)
                LOG.debug("DrugBank query result for %s: %s", drugA, db_enzymes_a)
                if db_enzymes_a and db_enzymes_a.get("enzymes"):
                    # Use enzyme_action_map if available (proper per-enzyme mapping)
                    enzyme_action_map = db_enzymes_a.get("enzyme_action_map", [])
                    enzymes_list = db_enzymes_a.get("enzymes", [])
                    
                    if enzyme_action_map:
                        # Use structured mapping (preferred)
                        for enzyme_entry in enzyme_action_map:
                            enzyme_name = enzyme_entry.get("enzyme", "")
                            actions = enzyme_entry.get("actions", [])
                            
                            # Extract CYP number if present
                            cyp_match = re.search(r"(?i)(?:cyp|p\s*450)\s*(\d+[a-z]?\d*)", enzyme_name, re.I)
                            if cyp_match:
                                cyp_canon = f"cyp{cyp_match.group(1).lower()}"
                                
                                # Categorize by all actions for this enzyme
                                for action in actions:
                                    if "substrate" in action.lower():
                                        enzymes_a["substrate"].append(cyp_canon)
                                    elif "inhibitor" in action.lower():
                                        enzymes_a["inhibitor"].append(cyp_canon)
                                    elif "inducer" in action.lower():
                                        enzymes_a["inducer"].append(cyp_canon)
                                
                                # If no actions specified, default to substrate
                                if not actions:
                                    enzymes_a["substrate"].append(cyp_canon)
                    else:
                        # Fallback to flat list (backward compatibility)
                        actions_list = db_enzymes_a.get("enzyme_actions", [])
                        for i, enzyme_name in enumerate(enzymes_list):
                            cyp_match = re.search(r"(?i)(?:cyp|p\s*450)\s*(\d+[a-z]?\d*)", enzyme_name, re.I)
                            if cyp_match:
                                cyp_canon = f"cyp{cyp_match.group(1).lower()}"
                                
                                action = None
                                if actions_list:
                                    if len(actions_list) < len(enzymes_list):
                                        action = actions_list[0] if actions_list else None
                                    else:
                                        action = actions_list[i] if i < len(actions_list) else None
                                
                                if action:
                                    if "substrate" in action.lower():
                                        enzymes_a["substrate"].append(cyp_canon)
                                    elif "inhibitor" in action.lower():
                                        enzymes_a["inhibitor"].append(cyp_canon)
                                    elif "inducer" in action.lower():
                                        enzymes_a["inducer"].append(cyp_canon)
                                    else:
                                        enzymes_a["substrate"].append(cyp_canon)
                                else:
                                    enzymes_a["substrate"].append(cyp_canon)
                    
                    if any(enzymes_a.values()):
                        LOG.info("Found %d enzymes from DrugBank for %s", len(enzymes_list), drugA)
            except Exception as e:
                LOG.warning("DrugBank enzyme query failed for %s: %s", drugA, e)
        
        # Check if QLever found any enzymes for drugB
        has_enzymes_b = any(enzymes_b.values())
        LOG.debug("DrugBank fallback check for %s: QLever found enzymes=%s", drugB, has_enzymes_b)
        if not has_enzymes_b:
            try:
                LOG.debug("Attempting DrugBank enzyme query for %s", drugB)
                db_enzymes_b = db_client.get_drug_enzymes(drugB)
                LOG.debug("DrugBank query result for %s: %s", drugB, db_enzymes_b)
                if db_enzymes_b and db_enzymes_b.get("enzymes"):
                    # Use enzyme_action_map if available (proper per-enzyme mapping)
                    enzyme_action_map = db_enzymes_b.get("enzyme_action_map", [])
                    enzymes_list = db_enzymes_b.get("enzymes", [])
                    
                    if enzyme_action_map:
                        # Use structured mapping (preferred)
                        for enzyme_entry in enzyme_action_map:
                            enzyme_name = enzyme_entry.get("enzyme", "")
                            actions = enzyme_entry.get("actions", [])
                            
                            # Extract CYP number if present
                            cyp_match = re.search(r"(?i)(?:cyp|p\s*450)\s*(\d+[a-z]?\d*)", enzyme_name, re.I)
                            if cyp_match:
                                cyp_canon = f"cyp{cyp_match.group(1).lower()}"
                                
                                # Categorize by all actions for this enzyme
                                for action in actions:
                                    if "substrate" in action.lower():
                                        enzymes_b["substrate"].append(cyp_canon)
                                    elif "inhibitor" in action.lower():
                                        enzymes_b["inhibitor"].append(cyp_canon)
                                    elif "inducer" in action.lower():
                                        enzymes_b["inducer"].append(cyp_canon)
                                
                                # If no actions specified, default to substrate
                                if not actions:
                                    enzymes_b["substrate"].append(cyp_canon)
                    else:
                        # Fallback to flat list (backward compatibility)
                        actions_list = db_enzymes_b.get("enzyme_actions", [])
                        for i, enzyme_name in enumerate(enzymes_list):
                            cyp_match = re.search(r"(?i)(?:cyp|p\s*450)\s*(\d+[a-z]?\d*)", enzyme_name, re.I)
                            if cyp_match:
                                cyp_canon = f"cyp{cyp_match.group(1).lower()}"
                                
                                action = None
                                if actions_list:
                                    if len(actions_list) < len(enzymes_list):
                                        action = actions_list[0] if actions_list else None
                                    else:
                                        action = actions_list[i] if i < len(actions_list) else None
                                
                                if action:
                                    if "substrate" in action.lower():
                                        enzymes_b["substrate"].append(cyp_canon)
                                    elif "inhibitor" in action.lower():
                                        enzymes_b["inhibitor"].append(cyp_canon)
                                    elif "inducer" in action.lower():
                                        enzymes_b["inducer"].append(cyp_canon)
                                    else:
                                        enzymes_b["substrate"].append(cyp_canon)
                                else:
                                    enzymes_b["substrate"].append(cyp_canon)
                    
                    if any(enzymes_b.values()):
                        LOG.info("Found %d enzymes from DrugBank for %s", len(enzymes_list), drugB)
            except Exception as e:
                LOG.warning("DrugBank enzyme query failed for %s: %s", drugB, e)
        
        # Optional: ChEMBL enrichment (if enabled)
        # Note: ChEMBL can provide data even when DrugBank doesn't, so we check ChEMBL
        # regardless of whether enzymes were found from DrugBank/QLever
        if chembl_enabled:
            try:
                from src.retrieval import chembl_client as chembl
                # Enrich after DrugBank fallback
                # ChEMBL may have data even if DrugBank doesn't, so always try
                if not chembl_data_a:  # Only query if not already set
                    chembl_data_a = chembl.enrich_mechanistic_data(drugA, enzymes_a)
                if not chembl_data_b:  # Only query if not already set
                    chembl_data_b = chembl.enrich_mechanistic_data(drugB, enzymes_b)
            except Exception as e:
                LOG.debug("ChEMBL enrichment failed: %s", e)
    except Exception as e:
        LOG.warning("Could not load DrugBank enzyme fallback: %s", e)

    # Targets (BIO) - with increased timeout for correctness
    # Try to get targets with labels (dict format), fallback to strings
    # Enrich with PubChem REST API for human-readable labels
    targets_a = []
    targets_b = []
    if cid_a:
        try:
            targets_a_dicts = _query_targets_for_cid(cid_a, limit=32, return_dicts=True)
            # ALWAYS enrich with PubChem REST API labels (not a fallback)
            from src.retrieval import pubchem_client as pc
            # Extract protein IDs from dicts
            protein_ids = []
            for t in targets_a_dicts:
                if isinstance(t, dict):
                    # Try to get ID from URI or label
                    uri = t.get("uri", "")
                    label = t.get("label", "")
                    # Extract ID from URI if it's a full URI
                    if uri and "/" in uri:
                        pid = uri.split("/")[-1]
                    elif label:
                        pid = label
                    else:
                        pid = str(t)
                    protein_ids.append(pid)
                else:
                    protein_ids.append(str(t))
            
            if protein_ids:
                try:
                    enriched_targets_a = pc.enrich_protein_ids(protein_ids)
                    # Update targets with enriched labels
                    for i, t in enumerate(targets_a_dicts):
                        if isinstance(t, dict) and i < len(enriched_targets_a):
                            enriched = enriched_targets_a[i]
                            if enriched.get("label") and enriched["label"] != enriched["id"]:
                                t["label"] = enriched["label"]
                                LOG.debug("Enriched protein label: %s -> %s", enriched["id"], enriched["label"])
                except Exception as e:
                    LOG.warning("PubChem protein label enrichment failed for %s: %s", drugA, e)
            
            # Convert to strings for backward compatibility, but preserve label info
            targets_a = [t.get("label", t.get("uri", "")) if isinstance(t, dict) else t for t in targets_a_dicts]
            if not targets_a:
                LOG.debug("No target data found for %s (CID %s)", drugA, cid_a)
        except Exception as e:
            LOG.warning("Target query failed for %s: %s", drugA, e)
            caveats.append(f"Target query failed for {drugA}: {e}")
    if cid_b:
        try:
            targets_b_dicts = _query_targets_for_cid(cid_b, limit=32, return_dicts=True)
            # ALWAYS enrich with PubChem REST API labels (not a fallback)
            from src.retrieval import pubchem_client as pc
            # Extract protein IDs from dicts
            protein_ids = []
            for t in targets_b_dicts:
                if isinstance(t, dict):
                    uri = t.get("uri", "")
                    label = t.get("label", "")
                    if uri and "/" in uri:
                        pid = uri.split("/")[-1]
                    elif label:
                        pid = label
                    else:
                        pid = str(t)
                    protein_ids.append(pid)
                else:
                    protein_ids.append(str(t))
            
            if protein_ids:
                try:
                    enriched_targets_b = pc.enrich_protein_ids(protein_ids)
                    # Update targets with enriched labels
                    for i, t in enumerate(targets_b_dicts):
                        if isinstance(t, dict) and i < len(enriched_targets_b):
                            enriched = enriched_targets_b[i]
                            if enriched.get("label") and enriched["label"] != enriched["id"]:
                                t["label"] = enriched["label"]
                                LOG.debug("Enriched protein label: %s -> %s", enriched["id"], enriched["label"])
                except Exception as e:
                    LOG.warning("PubChem protein label enrichment failed for %s: %s", drugB, e)
            
            targets_b = [t.get("label", t.get("uri", "")) if isinstance(t, dict) else t for t in targets_b_dicts]
            if not targets_b:
                LOG.debug("No target data found for %s (CID %s)", drugB, cid_b)
        except Exception as e:
            LOG.warning("Target query failed for %s: %s", drugB, e)
            caveats.append(f"Target query failed for {drugB}: {e}")

    # Diseases (DISEASE index) - NEW
    diseases_a = []
    diseases_b = []
    if cid_a:
        try:
            diseases_a = _query_diseases_for_cid(cid_a, limit=20)
            if not diseases_a:
                LOG.debug("No disease data found for %s (CID %s)", drugA, cid_a)
        except Exception as e:
            LOG.debug("Disease query failed for %s: %s", drugA, e)
            # Don't add to caveats - disease data may not be available for all compounds
    if cid_b:
        try:
            diseases_b = _query_diseases_for_cid(cid_b, limit=20)
            if not diseases_b:
                LOG.debug("No disease data found for %s (CID %s)", drugB, cid_b)
        except Exception as e:
            LOG.debug("Disease query failed for %s: %s", drugB, e)
            # Don't add to caveats - disease data may not be available for all compounds

    # ALWAYS fetch PK data from PubChem REST API (not a fallback)
    pk_data_a = {}
    pk_data_b = {}
    try:
        from src.retrieval import pubchem_client as pc
        # Get CID strings (remove "CID" prefix and URI parts if present)
        cid_a_str = None
        cid_b_str = None
        if cid_a:
            cid_a_str = cid_a.replace("CID", "").replace("http://rdf.ncbi.nlm.nih.gov/pubchem/compound/", "").replace("/", "").strip()
        if cid_b:
            cid_b_str = cid_b.replace("CID", "").replace("http://rdf.ncbi.nlm.nih.gov/pubchem/compound/", "").replace("/", "").strip()
        
        if cid_a_str:
            try:
                pk_data_a = pc.get_compound_pk_data(cid_a_str)
                if pk_data_a and any(v is not None for v in pk_data_a.values()):
                    LOG.info("Fetched PK data for %s (CID %s): %s", drugA, cid_a_str, [k for k, v in pk_data_a.items() if v is not None])
            except Exception as e:
                LOG.debug("PubChem PK data fetch failed for %s: %s", drugA, e)
        if cid_b_str:
            try:
                pk_data_b = pc.get_compound_pk_data(cid_b_str)
                if pk_data_b and any(v is not None for v in pk_data_b.values()):
                    LOG.info("Fetched PK data for %s (CID %s): %s", drugB, cid_b_str, [k for k, v in pk_data_b.items() if v is not None])
            except Exception as e:
                LOG.debug("PubChem PK data fetch failed for %s: %s", drugB, e)
    except Exception as e:
        LOG.warning("PubChem client import failed: %s", e)
    
    mech: Dict[str, Any] = {
        "enzymes": {"a": enzymes_a, "b": enzymes_b},
        "targets_a": targets_a,
        "targets_b": targets_b,
        "diseases_a": diseases_a,
        "diseases_b": diseases_b,
        "pathways_a": [],
        "pathways_b": [],
        "common_pathways": [],
        "ids_a": a_info.get("ids", {}),
        "ids_b": b_info.get("ids", {}),
        "synonyms_a": a_info.get("synonyms", []),
        "synonyms_b": b_info.get("synonyms", []),
        "pk_data_a": pk_data_a,  # NEW: PK data from PubChem REST API (always fetched)
        "pk_data_b": pk_data_b,  # NEW: PK data from PubChem REST API (always fetched)
        "caveats": caveats if caveats else [],
    }
    
    # Add ChEMBL enrichment if available
    if chembl_enabled and (chembl_data_a or chembl_data_b):
        mech["chembl_enrichment"] = {
            "a": chembl_data_a,
            "b": chembl_data_b,
        }
    
    return mech

def get_mechanistic_enriched(
    drugA: str,
    drugB: str,
    mg_list_a: Optional[List[str]] = None,
    mg_list_b: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Optional enrichment: when explicit BIO MeasureGroups are supplied, populate targets_* as dicts {uri,label}.
    """
    mech = get_mechanistic(drugA, drugB)

    def _targets_from_mgs(mg_list: List[str]) -> List[Dict[str, str]]:
        targets: List[Dict[str, str]] = []
        seen = set()
        for mg in mg_list:
            for row in bio_measuregroup_proteins(mg):
                uri = row.get("protein")
                if not uri:
                    continue
                uri = str(uri)
                label = str(row.get("protein_label") or uri)
                if uri not in seen:
                    seen.add(uri)
                    targets.append({"uri": uri, "label": label})
        return targets

    try:
        if mg_list_a:
            mech["targets_a"] = _targets_from_mgs(mg_list_a)
        if mg_list_b:
            mech["targets_b"] = _targets_from_mgs(mg_list_b)
        if mg_list_a or mg_list_b:
            mech.setdefault("caveats", []).append(
                "Targets derived from BIO endpoints via RO:0000057; interpret as putative."
            )
    except Exception:
        mech.setdefault("caveats", []).append("BIO enrichment failed; returning base schema.")
    return mech
