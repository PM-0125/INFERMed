"""
Thin QLever client + helpers for PubChem CORE and DISEASE endpoints.

Env:
  CORE_ENDPOINT=<qlever sparql endpoint url for core>
  DISEASE_ENDPOINT=<qlever sparql endpoint url for disease>

Quick usage:
  from src.retrieval.qlever_query import (
      get_clients_from_env,
      core_find_cid_by_exact_label,
      core_find_cid_by_label_fragment,
      core_descriptors_for_cids,
      core_xlogp_threshold,
      disease_find_by_label_fragment,
      disease_crossrefs,
  )
  core, disease = get_clients_from_env()
  print(core_find_cid_by_exact_label("Aspirin"))
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, cast

import requests

# ---------------------------------------------------------------------------
# Logging + endpoints (keep your normalization exactly as you had it)
LOG = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("QLEVER_CLIENT_LOGLEVEL", "WARNING").upper())

CORE_ENDPOINT = os.getenv("CORE_ENDPOINT", "").rstrip("/") + ("/" if os.getenv("CORE_ENDPOINT") else "")
DISEASE_ENDPOINT = os.getenv("DISEASE_ENDPOINT", "").rstrip("/") + ("/" if os.getenv("DISEASE_ENDPOINT") else "")

# ---------------------------------------------------------------------------
# Constants
PUBCHEM_COMPOUND_NS = "http://rdf.ncbi.nlm.nih.gov/pubchem/compound/"
SIO = "http://semanticscience.org/resource/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"

# ---------------------------------------------------------------------------
# Errors
class QLeverError(RuntimeError):
    pass


class QLeverTimeout(QLeverError):
    """Server-side cancellation (HTTP 429) or client-side read/connect timeout."""


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

    def query(self, sparql: str, retries: int = 0, backoff_s: float = 0.0) -> dict:
        """
        Execute a SPARQL query against a QLever server.

        Behavior:
        - HTTP 429 (QLever "Operation timed out") -> QLeverTimeout.
        - Client-side read/connect timeouts -> QLeverTimeout.
        - Connection errors -> QLeverError.
        - Other HTTP errors -> QLeverError (with short body snippet).
        """
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
                if resp.status_code == 429:
                    raise QLeverTimeout(self._extract_server_error(resp))
                resp.raise_for_status()
                return resp.json()

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
                status = resp.status_code if resp is not None else "?"
                try:
                    if resp is not None:
                        body = resp.text[:2000]
                except Exception:
                    pass
                if status == 429 and resp is not None:
                    raise QLeverTimeout(self._extract_server_error(resp))
                raise QLeverError(f"HTTP {status} from {self.endpoint}: {body}") from e

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


# ---------------------------------------------------------------------------
# Utilities

def _vals(bindings: Sequence[Dict[str, Any]], *cols: str) -> List[Tuple[str, ...]]:
    """Extract (string) tuple rows from SPARQL JSON bindings for the given columns."""
    out: List[Tuple[str, ...]] = []
    for b in bindings:
        row: List[str] = []
        for c in cols:
            cell = b.get(c)
            if not cell:
                break
            row.append(cell["value"])
        else:
            # only append if we didn't break (all columns present)
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
    """Convenience to fetch both clients; raises if env is missing."""
    return _ensure_client("core"), _ensure_client("disease")


def sparql_str(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{s}\""


# ---------------------------------------------------------------------------
# CORE helpers

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
        return cast(List[Tuple[str, str]], _vals(js["results"]["bindings"], "cid", "name"))
    except QLeverTimeout:
        LOG.warning("Fragment query timed out; falling back to exact label variants for %r", frag)

    # Fallback: probe exact-casing variants (fast)
    candidates = [frag, frag.capitalize(), frag.title(), frag.upper()]
    seen: set[str] = set()
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
    """
    Return {cid_uri: xlogp} for compounds with XLogP3 <= threshold.
    If the global scan times out, fall back to per-CID lookups for a small
    must-include set (fast because ?cid is bound).

    We also scope to the PubChem compound namespace to avoid accidental matches.
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
# Minimal mechanistic stub (PK/PD)
# Provides the shape expected by rag_pipeline. Enzymes/targets/pathways are left
# empty for now; but we resolve PubChem CIDs + collect a few synonyms.

def _first_cid_and_synonyms(name: str, limit: int = 25) -> tuple[Optional[str], Dict[str, Any]]:
    """
    Returns (cid_uri_or_none, info_dict).
    info_dict includes e.g. {"ids": {"pubchem_cid": "2244"}, "synonyms": ["Aspirin", ...]}
    """
    try:
        pairs = core_find_cid_by_label_fragment(name, limit=limit)
    except Exception:
        return None, {}

    if not pairs:
        return None, {}

    # Take the first result; gather synonyms (labels) for that same CID
    cid0, _ = pairs[0]
    syns = [label for cid, label in pairs if cid == cid0]

    # Extract numeric CID (PubChem URIs look like .../compound/CID2244)
    cid_num: Optional[str] = None
    m = re.search(r"CID(\d+)", cid0)
    if m:
        cid_num = m.group(1)

    ids = {"pubchem_cid": cid_num} if cid_num else {}
    return cid0, {"ids": ids, "synonyms": list(dict.fromkeys(syns))[:20]}


def get_mechanistic(drugA: str, drugB: str) -> Dict[str, Any]:
    """
    Minimal placeholder returning the correct schema:
      {
        "enzymes": {"a": {...}, "b": {...}},
        "targets_a": [...], "targets_b": [...],
        "pathways_a": [...], "pathways_b": [...],
        "common_pathways": [...],
        "ids_a": {...}, "ids_b": {...},
        "synonyms_a": [...], "synonyms_b": [...],
        "caveats": [...]
      }

    When you implement real SPARQL for enzymes/targets/pathways, fill those fields here.
    """
    caveats: List[str] = []

    # Ensure core endpoint exists early so callers can interpret caveats
    try:
        _ = _ensure_client("core")
    except Exception as e:
        return {
            "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                        "b": {"substrate": [], "inhibitor": [], "inducer": []}},
            "targets_a": [], "targets_b": [],
            "pathways_a": [], "pathways_b": [],
            "common_pathways": [],
            "ids_a": {}, "ids_b": {},
            "synonyms_a": [], "synonyms_b": [],
            "caveats": [f"QLever CORE unavailable: {e}"],
        }

    # Resolve CIDs + synonyms (best-effort)
    _, a_info = _first_cid_and_synonyms(drugA)
    _, b_info = _first_cid_and_synonyms(drugB)

    mech: Dict[str, Any] = {
        "enzymes": {
            "a": {"substrate": [], "inhibitor": [], "inducer": []},
            "b": {"substrate": [], "inhibitor": [], "inducer": []},
        },
        "targets_a": [], "targets_b": [],
        "pathways_a": [], "pathways_b": [],
        "common_pathways": [],
        "ids_a": a_info.get("ids", {}),
        "ids_b": b_info.get("ids", {}),
        "synonyms_a": a_info.get("synonyms", []),
        "synonyms_b": b_info.get("synonyms", []),
        "caveats": caveats or ["Mechanistic PK/PD not yet implemented; returning IDs/synonyms only."],
    }
    return mech
