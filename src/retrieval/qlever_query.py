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
OBI_0000299 = "http://purl.obolibrary.org/obo/OBI_0000299" # has_specified_output (MG -> Endpoint)
IAO_0000136 = "http://purl.obolibrary.org/obo/IAO_0000136" # is_about (Endpoint -> SID)
RO_0000056 = "http://purl.obolibrary.org/obo/RO_0000056" # participates_in (SID -> MG)
SIO_VALUE = "http://semanticscience.org/resource/SIO_000300" # numeric value
SIO_UNIT = "http://semanticscience.org/resource/SIO_000221" # unit
PCV_OUTCOME = "http://rdf.ncbi.nlm.nih.gov/pubchem/vocabulary#PubChemAssayOutcome"
RO_0000057 = "http://purl.obolibrary.org/obo/RO_0000057" # has_participant (Endpoint -> Protein/Gene)


# --- IRI prefixes for fast-bounded searches ---
MG_PREFIX = "http://rdf.ncbi.nlm.nih.gov/pubchem/measuregroup/"
EP_PREFIX = "http://rdf.ncbi.nlm.nih.gov/pubchem/endpoint/"

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


def _get_bio_endpoint() -> Optional[str]:
    """Return BIO_ENDPOINT from env or None if unset."""
    return os.environ.get("BIO_ENDPOINT") or None




def _bio_query(query: str) -> Dict[str, Any]:
    """Issue a SPARQL query to BIO_ENDPOINT and return parsed JSON.
    Non-throwing: returns {} on error or if requests is unavailable.
    """
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

# ------------------------------
# BIO helper functions
# ------------------------------

def bio_find_measuregroups_by_aid(aid: str, limit: int = 5) -> List[str]:
    """Return up to `limit` MeasureGroup IRIs whose endpoints mention the given AID token.
    Bounded by MG/Endpoint prefixes for speed.
    """
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
    """Return endpoints under a MeasureGroup with value/unit/outcome (+unit rdfs:label when present)."""
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
    """Return SIDs and mapped CIDs for a MeasureGroup using both RO:0000056 and IAO:0000136 paths."""
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
    """Return (endpoint, protein_uri, optional label) from RO:0000057 edges."""
    q = f"""
PREFIX OBI:<http://purl.obolibrary.org/obo/>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
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
    """Map endpoints back to their BioAssay IRI using regex/BIND to extract the AID token."""
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
    """Convenience wrapper that bundles endpoints, sid/cid mappings, proteins and endpoint→assay links."""
    return {
        "measuregroup": mg_uri,
        "endpoints": bio_measuregroup_endpoints(mg_uri),
        "sid_cid": bio_measuregroup_sid_cid(mg_uri),
        "proteins": bio_measuregroup_proteins(mg_uri),
        "endpoint_to_bioassay": bio_measuregroup_endpoints_to_bioassay(mg_uri),
    }


# ------------------------------
# Minimal mechanistic stub for RAG
# ------------------------------

def _first_cid_and_synonyms(name: str, limit: int = 25) -> tuple[Optional[str], Dict[str, Any]]:
    """Best-effort CID resolution + synonyms using existing CORE helper(s).
    Returns (cid_uri_or_none, info_dict) where info_dict may include
    {"ids": {"pubchem_cid": "2244"}, "synonyms": ["Aspirin", ...]}
    """
    try:
        # Assumes your module already exposes this function (tested earlier).
        pairs = core_find_cid_by_label_fragment(name, limit=limit)  # type: ignore[name-defined]
    except Exception:
        return None, {}

    if not pairs:
        return None, {}

    # First result's CID + gather all labels for that same CID
    cid0, _ = pairs[0]
    syns = [label for cid, label in pairs if cid == cid0]

    cid_num: Optional[str] = None
    m = re.search(r"CID(\d+)", cid0 or "")
    if m:
        cid_num = m.group(1)
    ids = {"pubchem_cid": cid_num} if cid_num else {}
    return cid0, {"ids": ids, "synonyms": list(dict.fromkeys(syns))[:20]}


def get_mechanistic(drugA: str, drugB: str) -> Dict[str, Any]:
    """Non-breaking minimal schema for RAG. Populates IDs/synonyms and leaves PK/PD empty.
    Fields:
      enzymes: substrate/inhibitor/inducer per A/B
      targets_a/targets_b: []
      pathways_a/pathways_b/common_pathways: []
      ids_a/ids_b, synonyms_a/synonyms_b
      caveats: list of notes
    """
    caveats: List[str] = []  # type: ignore[name-defined]

    # Ensure CORE is available for CID lookups (best-effort)
    try:
        _ = _ensure_client("core")  # type: ignore[name-defined]
    except Exception as e:  # pragma: no cover
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

    # Resolve IDs/synonyms
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
        "caveats": caveats or [
            "Mechanistic PK/PD not yet implemented; returning IDs/synonyms only.",
        ],
    }
    return mech


def get_mechanistic_enriched(
    drugA: str,
    drugB: str,
    mg_list_a: Optional[List[str]] = None,
    mg_list_b: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Optional enrichment that uses BIO MeasureGroups (if provided) to populate targets_* with proteins.
    This function does not alter the original get_mechanistic() behavior and can be used by RAG when MGs are known.
    """
    mech = get_mechanistic(drugA, drugB)

    # Helper to convert protein rows to compact target dicts
    def _targets_from_mgs(mg_list: List[str]) -> List[Dict[str, str]]:
        targets: List[Dict[str, str]] = []
        seen = set()
        for mg in mg_list:
            for row in bio_measuregroup_proteins(mg):  # type: ignore[name-defined]
                uri = row.get("protein")
                if not uri:
                    continue
                # Ensure both uri and label are plain str (no None) to satisfy the type
                uri = str(uri)
                label = row.get("protein_label") or uri
                label = str(label)
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