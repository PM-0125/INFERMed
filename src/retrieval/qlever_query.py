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
  QLEVER_TIMEOUT_CORE=30
  QLEVER_TIMEOUT_DISEASE=30
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
        return QLeverClient(CORE_ENDPOINT, timeout_s=int(os.getenv("QLEVER_TIMEOUT_CORE", "30")))
    elif which == "disease":
        if not DISEASE_ENDPOINT:
            raise QLeverError("DISEASE_ENDPOINT is not set in your environment.")
        return QLeverClient(DISEASE_ENDPOINT, timeout_s=int(os.getenv("QLEVER_TIMEOUT_DISEASE", "30")))
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
    try:
        r = requests.get(
            endpoint,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}

# ---------------------------------------------------------------------------
# CORE helpers (cached)

@lru_cache(maxsize=2048)
def core_find_cid_by_exact_label(label: str, limit: int = 50) -> List[str]:
    cli = _ensure_client("core")
    q = f"""
PREFIX skos:<{SKOS}>
SELECT ?cid WHERE {{
  ?cid skos:prefLabel {sparql_str(label)}
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
  BIND(REPLACE(STR(?attr), '.*/', '') AS ?key)
  FILTER(REGEX(?key, '_(Canonical_SMILES|Isomeric_SMILES|IUPAC_InChI|Molecular_Formula|Molecular_Weight|Exact_Mass|TPSA|Hydrogen_Bond_Acceptor_Count|Hydrogen_Bond_Donor_Count|Rotatable_Bond_Count|XLogP3)$'))
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
    Heuristic enzyme role extraction; PubChem RDF modeling can vary.
    Returns dict with 'substrate','inhibitor','inducer' lists (strings).
    """
    if not cid_uri:
        return {"substrate": [], "inhibitor": [], "inducer": []}

    cli = _ensure_client("core")
    enzymes = {"substrate": [], "inhibitor": [], "inducer": []}

    queries = {
        "substrate": f"""
PREFIX sio:<{SIO}>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?enzyme ?label WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  ?attr sio:SIO_000300 ?enzyme .
  FILTER(REGEX(STR(?enzyme), "(?i)(cyp|cytochrome|p450)"))
  OPTIONAL {{ ?enzyme rdfs:label ?label }}
}}
LIMIT 100
""",
        "inhibitor": f"""
PREFIX sio:<{SIO}>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?enzyme ?label WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  ?attr sio:SIO_000300 ?enzyme .
  FILTER(REGEX(STR(?attr), "(?i)(inhibit|inhibition)"))
  FILTER(REGEX(STR(?enzyme), "(?i)(cyp|cytochrome|p450)"))
  OPTIONAL {{ ?enzyme rdfs:label ?label }}
}}
LIMIT 100
""",
        "inducer": f"""
PREFIX sio:<{SIO}>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?enzyme ?label WHERE {{
  <{cid_uri}> sio:SIO_000008 ?attr .
  ?attr sio:SIO_000300 ?enzyme .
  FILTER(REGEX(STR(?attr), "(?i)(induc|induction)"))
  FILTER(REGEX(STR(?enzyme), "(?i)(cyp|cytochrome|p450)"))
  OPTIONAL {{ ?enzyme rdfs:label ?label }}
}}
LIMIT 100
"""
    }

    for role, query in queries.items():
        try:
            js = cli.query(query)
            for b in js.get("results", {}).get("bindings", []):
                enzyme = b.get("enzyme", {}).get("value", "")
                label = b.get("label", {}).get("value", "")
                if label:
                    enzymes[role].append(label)
                elif enzyme:
                    m = re.search(r"(?i)(cyp\d+[a-z]?\d*)", enzyme)
                    if m:
                        enzymes[role].append(m.group(1).upper())
        except Exception:
            continue

    for role in enzymes:
        enzymes[role] = _normalize_syns(enzymes[role])
    return enzymes

@lru_cache(maxsize=4096)
def _query_targets_for_cid(cid_uri: str, limit: int = 32) -> List[str]:
    """
    Query BIO index for protein targets associated with a CID via MeasureGroups.
    Covers both:
      A) Endpoint --IAO:0000136--> SID --CHEMINF_000477--> CID
      B) Substance --substance2compound--> CID ; Substance --substance2measuregroup--> MG
    Returns list of protein labels (fallback to URI suffix).
    """
    if not cid_uri or not _get_bio_endpoint():
        return []

    q = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
PREFIX IAO:<http://purl.obolibrary.org/obo/>
PREFIX rdfs:<{RDFS}>
SELECT DISTINCT ?prot ?prot_label WHERE {{
  {{
    ?mg OBI:OBI_0000299 ?e .
    ?e <{IAO_0000136}> ?sid .
    ?sid <http://semanticscience.org/resource/CHEMINF_000477> <{cid_uri}> .
    ?e <{RO_0000057}> ?prot .
  }}
  UNION
  {{
    ?sub <http://semanticscience.org/resource/CHEMINF_000477> <{cid_uri}> .
    ?sub <http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#substance2measuregroup> ?mg .
    ?mg  OBI:OBI_0000299 ?e .
    ?e   <{RO_0000057}> ?prot .
  }}
  OPTIONAL {{ ?prot rdfs:label ?prot_label }}
}}
LIMIT {int(limit) * 3}
"""
    try:
        data = _bio_query(q)
        seen, targets = set(), []
        for b in data.get("results", {}).get("bindings", []):
            prot_uri = (b.get("prot", {}) or {}).get("value")
            label = (b.get("prot_label", {}) or {}).get("value")
            if not prot_uri or prot_uri in seen:
                continue
            seen.add(prot_uri)
            if label:
                targets.append(label)
            else:
                targets.append(prot_uri.rsplit("/", 1)[-1])
            if len(targets) >= limit:
                break
        return _normalize_syns(targets)
    except Exception as e:
        LOG.warning("BIO target query failed for %s: %s", cid_uri, e)
        return []

# ---------------------------------------------------------------------------
# Public: mechanistic bundles

def get_mechanistic(drugA: str, drugB: str) -> Dict[str, Any]:
    """
    Retrieve mechanistic PK/PD data from QLever indices.
    Returns dict:
      enzymes: {'a': {'substrate':[], 'inhibitor':[], 'inducer':[]}, 'b': {...}}
      targets_a / targets_b: [str, ...]
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
            "pathways_a": [], "pathways_b": [], "common_pathways": [],
            "ids_a": {}, "ids_b": {},
            "synonyms_a": [], "synonyms_b": [],
            "caveats": [f"QLever CORE unavailable: {e}"],
        }

    # IDs & synonyms
    cid_a, a_info = _first_cid_and_synonyms(drugA)
    cid_b, b_info = _first_cid_and_synonyms(drugB)

    # Enzymes
    enzymes_a = {"substrate": [], "inhibitor": [], "inducer": []}
    enzymes_b = {"substrate": [], "inhibitor": [], "inducer": []}
    if cid_a:
        try:
            enzymes_a = _query_enzymes_for_cid(cid_a)
            if not any(enzymes_a.values()):
                LOG.debug("No enzyme data for %s (CID %s)", drugA, cid_a)
        except Exception as e:
            LOG.warning("Enzyme query failed for %s: %s", drugA, e)
            caveats.append(f"Enzyme query failed for {drugA}: {e}")
    if cid_b:
        try:
            enzymes_b = _query_enzymes_for_cid(cid_b)
            if not any(enzymes_b.values()):
                LOG.debug("No enzyme data for %s (CID %s)", drugB, cid_b)
        except Exception as e:
            LOG.warning("Enzyme query failed for %s: %s", drugB, e)
            caveats.append(f"Enzyme query failed for {drugB}: {e}")

    # Targets (BIO)
    targets_a = _query_targets_for_cid(cid_a, limit=32) if cid_a else []
    targets_b = _query_targets_for_cid(cid_b, limit=32) if cid_b else []

    return {
        "enzymes": {"a": enzymes_a, "b": enzymes_b},
        "targets_a": targets_a,
        "targets_b": targets_b,
        "pathways_a": [],
        "pathways_b": [],
        "common_pathways": [],
        "ids_a": a_info.get("ids", {}),
        "ids_b": b_info.get("ids", {}),
        "synonyms_a": a_info.get("synonyms", []),
        "synonyms_b": b_info.get("synonyms", []),
        "caveats": caveats if caveats else [],
    }

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
