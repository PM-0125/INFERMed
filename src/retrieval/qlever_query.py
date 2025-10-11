"""
Thin QLever client + helpers for PubChem CORE and DISEASE endpoints.

Env:
  CORE_ENDPOINT=<qlever sparql endpoint url for core>
  DISEASE_ENDPOINT=<qlever sparql endpoint url for disease>

Usage (quick):
  from qlever_query import get_clients_from_env, core_find_exact_by_prefLabel
  core, disease = get_clients_from_env()
  print(core_find_exact_by_prefLabel(core, "Aspirin"))
"""

# qlever_query.py
# Lightweight helpers for querying your local QLever endpoints (core + disease)
# and shaping results for tests and downstream use.
# qlever_query.py
from __future__ import annotations

import os
import re
import time
import logging
from typing import Dict, Iterable, List, Optional, Tuple

import requests

LOG = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("QLEVER_CLIENT_LOGLEVEL", "WARNING").upper())

CORE_ENDPOINT = os.getenv("CORE_ENDPOINT", "").rstrip("/") + ("/" if os.getenv("CORE_ENDPOINT") else "")
DISEASE_ENDPOINT = os.getenv("DISEASE_ENDPOINT", "").rstrip("/") + ("/" if os.getenv("DISEASE_ENDPOINT") else "")

PUBCHEM_COMPOUND_NS = "http://rdf.ncbi.nlm.nih.gov/pubchem/compound/"
SIO = "http://semanticscience.org/resource/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"


class QLeverError(RuntimeError):
    pass


class QLeverTimeout(QLeverError):
    pass


class QLeverClient:
    def __init__(self, endpoint: str, timeout_s: int = 30, session: Optional[requests.Session] = None):
        if not endpoint:
            raise ValueError("QLever endpoint is empty.")
        self.endpoint = endpoint.rstrip("/") + "/"
        self.timeout_s = timeout_s
        self.sess = session or requests.Session()
        self._headers = {"Accept": "application/sparql-results+json"}

    def query(self, sparql: str, retries: int = 0, backoff_s: float = 0.0) -> dict:
        """
        Execute a SPARQL query against a QLever server.
        - Treat HTTP 429 as QLeverTimeout (server cancelled query).
        - Treat client-side read/connect timeouts as QLeverTimeout as well,
          so callers can trigger their fallbacks.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                r = self.sess.get(
                    self.endpoint,
                    params={"query": sparql},
                    headers=self._headers,
                    timeout=self.timeout_s,
                )
                if r.status_code == 429:
                    raise QLeverTimeout(self._extract_server_error(r))
                r.raise_for_status()
                return r.json()

            except requests.Timeout as e:
                # Surface client timeouts as QLeverTimeout so callers can fallback.
                last_exc = e
                if attempt < retries:
                    time.sleep(backoff_s or 0.2 * (attempt + 1))
                    continue
                raise QLeverTimeout(f"Client-side timeout contacting {self.endpoint}: {e}") from e

            except requests.ConnectionError as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(backoff_s or 0.2 * (attempt + 1))
                    continue
                raise QLeverError(f"HTTP error contacting {self.endpoint}: {e}") from e

            except requests.HTTPError as e:
                body = ""
                try:
                    body = r.text[:2000]  # type: ignore[attr-defined]
                except Exception:
                    pass
                if r.status_code == 429:
                    raise QLeverTimeout(self._extract_server_error(r))
                raise QLeverError(f"HTTP {r.status_code} from {self.endpoint}: {body}") from e

        # Should never reach here
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
            return f"429 from QLever (text): {r.text[:2000]}"


def _vals(bindings: List[dict], *cols: str) -> List[Tuple[str, ...]]:
    out: List[Tuple[str, ...]] = []
    for b in bindings:
        tup = []
        ok = True
        for c in cols:
            cell = b.get(c)
            if not cell:
                ok = False
                break
            tup.append(cell["value"])
        if ok:
            out.append(tuple(tup))
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
        return _vals(js["results"]["bindings"], "cid", "name")
    except QLeverTimeout:
        LOG.warning("Fragment query timed out; falling back to exact label variants for %r", frag)

    candidates = [frag, frag.capitalize(), frag.title(), frag.upper()]
    seen = set()
    out: List[Tuple[str, str]] = []
    for c in candidates:
        for cid in core_find_cid_by_exact_label(c, limit=limit):
            if cid not in seen:
                out.append((cid, c))
                seen.add(cid)
    return out


def core_descriptors_for_cids(cids: Iterable[str]) -> Dict[str, Dict[str, str]]:
    cids = list(dict.fromkeys(cids))
    if not cids:
        return {}

    cli = _ensure_client("core")
    values = " ".join(f"<{cid}>" for cid in cids)
    q = f"""
PREFIX sio:<{SIO}>
PREFIX dct:<http://purl.org/dc/terms/>
PREFIX skos:<{SKOS}>
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


def core_xlogp_threshold(max_xlogp: float, limit: int = 1000,
                         must_include_cids: Optional[List[str]] = None) -> Dict[str, float]:
    """
    Try to obtain a global slice of compounds with XLogP3 <= threshold.
    If the global scan times out (common on large indexes), fall back to
    per-CID lookups for a small must-include list (fast, because ?cid is bound).
    """
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
        # Ensure key examples are present if they qualify
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
        # --- Fallback: per-CID lookups (fast) ---------------------------------
        fallback_cids = must_include_cids or [
            f"{PUBCHEM_COMPOUND_NS}CID2244",  # Aspirin
            f"{PUBCHEM_COMPOUND_NS}CID1000",  # Phenylethanolamine
        ]
        results: Dict[str, float] = {}
        for cid in fallback_cids:
            v = _core_get_single_descriptor_value(cid, "XLogP3")
            if v is None:
                continue
            try:
                fv = float(v)
            except ValueError:
                continue
            if fv <= max_xlogp:
                results[cid] = fv
        return results


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
    return _vals(js["results"]["bindings"], "d", "label")


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


def sparql_str(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{s}\""

