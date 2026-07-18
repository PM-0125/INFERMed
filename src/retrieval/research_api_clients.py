from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from src.utils.caching import load_json, save_json

LOG = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 14 * 24 * 3600
DEFAULT_TIMEOUT = 12

EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OPEN_TARGETS_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"
STRING_API_BASE = "https://version-12-0.string-db.org/api/json"
BIOGRID_BASE = "https://webservice.thebiogrid.org"
DRUGCENTRAL_API_BASE = "https://uxn2ycvimg.us-east-2.awsapprunner.com"

FDA_PGX_URLS = (
    "https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenomic-biomarkers-drug-labeling",
    "https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenetic-associations",
)


def _clean_text(value: Any, *, max_chars: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _dedupe_rows(rows: list[dict[str, Any]], *, key_fields: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(str(row.get(field) or "").lower() for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _request_json_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int,
    **kwargs: Any,
) -> dict[str, Any] | list[Any]:
    for attempt in range(3):
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, (dict, list)):
                    return data
                return {}
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(0.5 * (2**attempt))
                continue
            LOG.debug("Request to %s returned status %s", url, response.status_code)
            return {}
        except (requests.RequestException, ValueError) as exc:
            LOG.debug("Request to %s failed: %s", url, exc)
            time.sleep(0.5 * (2**attempt))
    return {}


class EuropePMCClient:
    """Query Europe PMC literature metadata for pair-specific DDI context."""

    def __init__(
        self,
        cache_dir: str = "data/cache/europepmc",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self._session = requests.Session()

    def search_interaction_literature(self, drug_a: str, drug_b: str, *, limit: int = 5) -> dict[str, Any]:
        a = str(drug_a or "").strip()
        b = str(drug_b or "").strip()
        if not a or not b:
            return {"found": False, "articles": [], "query": ""}

        key = f"europepmc__ddi__{a}__{b}__{limit}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        query = (
            f'("{a}" AND "{b}") AND '
            '("drug interaction" OR pharmacokinetic OR pharmacodynamic OR adverse OR toxicity)'
        )
        payload = _request_json_with_retries(
            self._session,
            "GET",
            EUROPE_PMC_BASE,
            timeout=self.timeout,
            params={
                "query": query,
                "format": "json",
                "pageSize": str(max(1, min(limit, 10))),
                "resultType": "core",
            },
        )
        articles: list[dict[str, Any]] = []
        for row in ((payload or {}).get("resultList") or {}).get("result", []) if isinstance(payload, dict) else []:
            if not isinstance(row, dict):
                continue
            pmid = str(row.get("pmid") or "").strip()
            pmcid = str(row.get("pmcid") or "").strip()
            doi = str(row.get("doi") or "").strip()
            url = ""
            if pmcid:
                url = f"https://europepmc.org/article/PMC/{pmcid.replace('PMC', '')}"
            elif pmid:
                url = f"https://europepmc.org/article/MED/{pmid}"
            elif doi:
                url = f"https://doi.org/{doi}"
            articles.append(
                {
                    "title": _clean_text(row.get("title"), max_chars=240),
                    "year": str(row.get("pubYear") or ""),
                    "journal": _clean_text(row.get("journalTitle"), max_chars=120),
                    "pmid": pmid,
                    "pmcid": pmcid,
                    "doi": doi,
                    "url": url,
                }
            )
        articles = _dedupe_rows(articles, key_fields=("title", "pmid", "doi"), limit=limit)
        out = {
            "query": query,
            "found": bool(articles),
            "articles": articles,
            "provenance": {
                "source": "Europe PMC REST API",
                "source_url": "https://europepmc.org/RestfulWebService",
                "api_endpoint": EUROPE_PMC_BASE,
            },
            "limitations": [
                "Literature metadata is not itself clinical proof; inspect the article before using a claim.",
                "Search results can include broad reviews, unrelated mentions, or non-DDI contexts.",
            ],
        }
        save_json(self.cache_dir, key, out)
        return out


class StringDBClient:
    """STRING protein association lookups for mechanism hypotheses."""

    def __init__(
        self,
        cache_dir: str = "data/cache/stringdb",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
        caller_identity: str = "infermed-research",
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self.caller_identity = caller_identity
        self._session = requests.Session()

    def get_network_summary(self, identifiers: list[str], *, species: int = 9606, limit: int = 12) -> dict[str, Any]:
        seeds = [str(item).strip() for item in identifiers if str(item or "").strip()]
        seeds = list(dict.fromkeys(seeds))[:limit]
        if not seeds:
            return {"found": False, "mapped": [], "interactions": [], "query_identifiers": []}

        key = f"stringdb__network__{species}__{'__'.join(seeds)}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        mapped_payload = self._post(
            "get_string_ids",
            {
                "identifiers": "\r".join(seeds),
                "species": str(species),
                "limit": "1",
                "caller_identity": self.caller_identity,
            },
        )
        mapped_rows: list[dict[str, Any]] = []
        for row in mapped_payload if isinstance(mapped_payload, list) else []:
            if not isinstance(row, dict):
                continue
            mapped_rows.append(
                {
                    "query": str(row.get("queryItem") or ""),
                    "string_id": str(row.get("stringId") or ""),
                    "preferred_name": str(row.get("preferredName") or ""),
                    "annotation": _clean_text(row.get("annotation"), max_chars=160),
                }
            )

        string_ids = [row["string_id"] for row in mapped_rows if row.get("string_id")]
        network_payload: dict[str, Any] | list[Any] = []
        if string_ids:
            network_payload = self._post(
                "network",
                {
                    "identifiers": "\r".join(string_ids[:limit]),
                    "species": str(species),
                    "caller_identity": self.caller_identity,
                },
            )

        interactions: list[dict[str, Any]] = []
        for row in network_payload if isinstance(network_payload, list) else []:
            if not isinstance(row, dict):
                continue
            interactions.append(
                {
                    "protein_a": str(row.get("preferredName_A") or row.get("stringId_A") or ""),
                    "protein_b": str(row.get("preferredName_B") or row.get("stringId_B") or ""),
                    "score": row.get("score"),
                    "annotation_a": _clean_text(row.get("annotation_A"), max_chars=140),
                    "annotation_b": _clean_text(row.get("annotation_B"), max_chars=140),
                }
            )
        interactions = _dedupe_rows(interactions, key_fields=("protein_a", "protein_b"), limit=limit)
        out = {
            "query_identifiers": seeds,
            "found": bool(mapped_rows or interactions),
            "mapped": mapped_rows[:limit],
            "interactions": interactions,
            "provenance": {
                "source": "STRING API",
                "source_url": "https://string-db.org/help/api/",
                "api_base": STRING_API_BASE,
                "species": species,
            },
            "limitations": [
                "STRING associations support biological hypothesis generation, not clinical causality.",
                "Scores summarize protein-association evidence and are not drug-interaction risk scores.",
            ],
        }
        save_json(self.cache_dir, key, out)
        return out

    def _post(self, method: str, data: dict[str, str]) -> dict[str, Any] | list[Any]:
        time.sleep(1.0)
        return _request_json_with_retries(
            self._session,
            "POST",
            f"{STRING_API_BASE}/{method}",
            timeout=self.timeout,
            data=data,
        )


class DrugCentralClient:
    """Public DrugCentral API wrapper for drug structure and target context."""

    def __init__(
        self,
        cache_dir: str = "data/cache/drugcentral",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self._session = requests.Session()

    def get_drug_summary(self, drug: str, *, target_limit: int = 12) -> dict[str, Any]:
        name = str(drug or "").strip()
        if not name:
            return {"found": False, "drug": "", "structure": {}, "targets": []}

        key = f"drugcentral__summary__{name}__{target_limit}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        structures_payload = _request_json_with_retries(
            self._session,
            "GET",
            f"{DRUGCENTRAL_API_BASE}/structures/name/{quote(name, safe='')}",
            timeout=self.timeout,
        )
        structures = structures_payload if isinstance(structures_payload, list) else []
        selected = self._select_structure(name, structures)
        targets: list[dict[str, Any]] = []
        if selected.get("id") is not None:
            targets_payload = _request_json_with_retries(
                self._session,
                "GET",
                f"{DRUGCENTRAL_API_BASE}/act_table_full/struct_id/{selected['id']}",
                timeout=self.timeout,
            )
            targets = self._normalize_targets(targets_payload if isinstance(targets_payload, list) else [], target_limit)

        out = {
            "drug": name,
            "found": bool(selected),
            "structure": self._normalize_structure(selected),
            "targets": targets,
            "provenance": {
                "source": "DrugCentral DRS API",
                "source_url": "https://drugcentral.org/download",
                "api_base": DRUGCENTRAL_API_BASE,
            },
            "limitations": [
                "DrugCentral target and activity rows support mechanism review; they do not by themselves prove a DDI.",
                "Activity rows may come from heterogeneous literature, labels, and external sources.",
            ],
        }
        save_json(self.cache_dir, key, out)
        return out

    @staticmethod
    def _select_structure(name: str, rows: list[Any]) -> dict[str, Any]:
        candidates = [row for row in rows if isinstance(row, dict)]
        if not candidates:
            return {}
        lowered = name.lower()
        for row in candidates:
            if str(row.get("name") or "").strip().lower() == lowered:
                return row
        return candidates[0]

    @staticmethod
    def _normalize_structure(row: dict[str, Any]) -> dict[str, Any]:
        if not row:
            return {}
        return {
            "id": row.get("id"),
            "cd_id": row.get("cd_id"),
            "name": str(row.get("name") or ""),
            "cas_reg_no": str(row.get("cas_reg_no") or ""),
            "formula": str(row.get("cd_formula") or ""),
            "molecular_weight": row.get("cd_molweight"),
            "smiles": str(row.get("smiles") or ""),
            "inchikey": str(row.get("inchikey") or ""),
            "definition": _clean_text(row.get("mrdef"), max_chars=420),
            "fda_labels": row.get("fda_labels"),
        }

    @staticmethod
    def _normalize_targets(rows: list[Any], limit: int) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            targets.append(
                {
                    "gene": str(row.get("gene") or ""),
                    "target_name": str(row.get("target_name") or ""),
                    "accession": str(row.get("accession") or ""),
                    "swissprot": str(row.get("swissprot") or ""),
                    "target_class": str(row.get("target_class") or ""),
                    "organism": str(row.get("organism") or ""),
                    "action_type": str(row.get("action_type") or ""),
                    "act_type": str(row.get("act_type") or ""),
                    "act_value": row.get("act_value"),
                    "relation": str(row.get("relation") or ""),
                    "act_source": str(row.get("act_source") or ""),
                    "act_source_url": str(row.get("act_source_url") or ""),
                    "moa": row.get("moa"),
                    "moa_source": str(row.get("moa_source") or ""),
                }
            )
        return _dedupe_rows(targets, key_fields=("gene", "target_name", "act_type", "act_value"), limit=limit)


class OpenTargetsClient:
    """Best-effort Open Targets Platform GraphQL search."""

    def __init__(
        self,
        cache_dir: str = "data/cache/opentargets",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self._session = requests.Session()

    def search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        term = str(query or "").strip()
        if not term:
            return {"found": False, "hits": [], "query": ""}

        key = f"opentargets__search__{term}__{limit}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        gql = """
        query Search($queryString: String!) {
          search(queryString: $queryString) {
            hits {
              id
              name
              entity
              description
            }
          }
        }
        """
        payload = _request_json_with_retries(
            self._session,
            "POST",
            OPEN_TARGETS_GRAPHQL,
            timeout=self.timeout,
            json={"query": gql, "variables": {"queryString": term}},
            headers={"Content-Type": "application/json"},
        )
        errors = payload.get("errors") if isinstance(payload, dict) else None
        rows = (((payload or {}).get("data") or {}).get("search") or {}).get("hits") if isinstance(payload, dict) else []
        hits: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            hits.append(
                {
                    "id": str(row.get("id") or ""),
                    "name": str(row.get("name") or ""),
                    "entity": str(row.get("entity") or ""),
                    "description": _clean_text(row.get("description"), max_chars=220),
                }
            )
        hits = _dedupe_rows(hits, key_fields=("id", "name", "entity"), limit=limit)
        out = {
            "query": term,
            "found": bool(hits),
            "hits": hits,
            "errors": errors or [],
            "provenance": {
                "source": "Open Targets GraphQL API",
                "source_url": "https://platform.opentargets.org/",
                "api_endpoint": OPEN_TARGETS_GRAPHQL,
            },
            "limitations": [
                "Open Targets search results are context for biology and evidence discovery, not DDI proof.",
                "Entity search can return targets, drugs, diseases, and other entities; downstream review is required.",
            ],
        }
        save_json(self.cache_dir, key, out)
        return out


class FDAPGxClient:
    """Small FDA PGx page matcher for label biomarker context."""

    def __init__(
        self,
        cache_dir: str = "data/cache/fda_pgx",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self._session = requests.Session()

    def get_pair_matches(self, drug_a: str, drug_b: str) -> dict[str, Any]:
        a = str(drug_a or "").strip()
        b = str(drug_b or "").strip()
        if not a and not b:
            return {"found": False, "a": [], "b": []}

        key = f"fda_pgx__pair__{a}__{b}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        pages = [self._page_text(index, url) for index, url in enumerate(FDA_PGX_URLS)]
        out = {
            "found": False,
            "a": self._matches_for_drug(a, pages) if a else [],
            "b": self._matches_for_drug(b, pages) if b else [],
            "provenance": {
                "source": "FDA pharmacogenomic biomarker pages",
                "source_url": FDA_PGX_URLS[0],
                "additional_urls": list(FDA_PGX_URLS[1:]),
            },
            "limitations": [
                "This is lightweight page matching, not a structured FDA PGx table parser.",
                "A match means the drug appears in FDA PGx reference pages; it does not by itself define a DDI.",
            ],
        }
        out["found"] = bool(out["a"] or out["b"])
        save_json(self.cache_dir, key, out)
        return out

    def _page_text(self, index: int, url: str) -> dict[str, str]:
        key = f"fda_pgx__page__{index}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return {"url": str(cached.get("url") or url), "text": str(cached.get("text") or "")}
        text = ""
        for attempt in range(3):
            try:
                response = self._session.get(url, timeout=self.timeout)
                if response.status_code == 200:
                    text = _html_to_text(response.text)
                    break
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(0.5 * (2**attempt))
                    continue
                break
            except requests.RequestException as exc:
                LOG.debug("FDA PGx page request failed for %s: %s", url, exc)
                time.sleep(0.5 * (2**attempt))
        payload = {"url": url, "text": text}
        save_json(self.cache_dir, key, payload)
        return payload

    @staticmethod
    def _matches_for_drug(drug: str, pages: list[dict[str, str]]) -> list[dict[str, str]]:
        if not drug:
            return []
        pattern = re.compile(re.escape(drug), re.IGNORECASE)
        rows: list[dict[str, str]] = []
        for page in pages:
            text = page.get("text") or ""
            for match in pattern.finditer(text):
                start = max(0, match.start() - 180)
                end = min(len(text), match.end() + 220)
                rows.append(
                    {
                        "drug": drug,
                        "snippet": _clean_text(text[start:end], max_chars=360),
                        "url": page.get("url") or "",
                    }
                )
                if len(rows) >= 3:
                    return rows
        return rows


class BioGRIDClient:
    """Credential-gated BioGRID REST wrapper for gene/protein interactions."""

    def __init__(
        self,
        access_key: str,
        cache_dir: str = "data/cache/biogrid",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.access_key = str(access_key or "").strip()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self._session = requests.Session()

    def get_interactions(self, identifiers: list[str], *, tax_id: int = 9606, limit: int = 20) -> dict[str, Any]:
        seeds = [str(item).strip() for item in identifiers if str(item or "").strip()]
        seeds = list(dict.fromkeys(seeds))[:limit]
        if not self.access_key:
            return {
                "found": False,
                "available": False,
                "reason": "BIOGRID_ACCESS_KEY is not configured.",
                "interactions": [],
            }
        if not seeds:
            return {"found": False, "available": True, "interactions": [], "query_identifiers": []}

        key = f"biogrid__interactions__{tax_id}__{'__'.join(seeds)}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        payload = _request_json_with_retries(
            self._session,
            "GET",
            f"{BIOGRID_BASE}/interactions/",
            timeout=self.timeout,
            params={
                "accesskey": self.access_key,
                "format": "json",
                "searchNames": "true",
                "geneList": "|".join(seeds),
                "taxId": str(tax_id),
                "max": str(limit),
            },
        )
        rows: list[dict[str, Any]] = []
        source_rows = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "interactor_a": str(row.get("OFFICIAL_SYMBOL_A") or row.get("BIOGRID_ID_A") or ""),
                    "interactor_b": str(row.get("OFFICIAL_SYMBOL_B") or row.get("BIOGRID_ID_B") or ""),
                    "experimental_system": str(row.get("EXPERIMENTAL_SYSTEM") or ""),
                    "throughput": str(row.get("THROUGHPUT") or ""),
                    "pubmed_id": str(row.get("PUBMED_ID") or ""),
                }
            )
        rows = _dedupe_rows(rows, key_fields=("interactor_a", "interactor_b", "experimental_system", "pubmed_id"), limit=limit)
        out = {
            "query_identifiers": seeds,
            "found": bool(rows),
            "available": True,
            "interactions": rows,
            "provenance": {
                "source": "BioGRID REST API",
                "source_url": "https://wiki.thebiogrid.org/doku.php/biogridrest",
                "api_base": BIOGRID_BASE,
            },
            "limitations": [
                "BioGRID reports biological interactions between gene/protein products, not direct drug-drug interactions.",
                "Use as mechanistic hypothesis context only.",
            ],
        }
        save_json(self.cache_dir, key, out)
        return out


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _clean_text(text, max_chars=120000)
