from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests

from src.utils.caching import load_json, save_json

LOG = logging.getLogger(__name__)

RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"
DEFAULT_CACHE_DIR = "data/cache/rxnav"
DEFAULT_TTL_SECONDS = 30 * 24 * 3600
DEFAULT_TIMEOUT = 10
RATE_LIMIT_SECONDS = 0.15


class RxNormClient:
    """Small public RxNav/RxNorm wrapper for drug identity and class context."""

    def __init__(
        self,
        cache_dir: str = DEFAULT_CACHE_DIR,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self._session = requests.Session()

    def resolve_drug(self, drug_name: str) -> dict[str, Any]:
        name = str(drug_name or "").strip()
        if not name:
            return {"query": drug_name, "resolved": False}

        key = f"rxnorm_v2__resolve__{name}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        rxcui = self._find_rxcui(name)
        payload: dict[str, Any] = {
            "query": name,
            "resolved": bool(rxcui),
            "rxcui": rxcui,
            "name": name,
            "tty": None,
            "synonym": None,
            "ingredients": [],
            "classes": [],
            "provenance": {
                "source": "RxNorm/RxClass API",
                "source_url": "https://rxnav.nlm.nih.gov/",
            },
        }

        if rxcui:
            props = self._properties(rxcui)
            if props:
                payload["name"] = props.get("name") or payload["name"]
                payload["tty"] = props.get("tty")
                payload["synonym"] = props.get("synonym")
            payload["ingredients"] = self._related_concepts(rxcui, tty="IN+PIN")
            payload["classes"] = self._classes(rxcui)

        save_json(self.cache_dir, key, payload)
        return payload

    def _request_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{RXNAV_BASE}/{path.lstrip('/')}"
        for attempt in range(3):
            try:
                time.sleep(RATE_LIMIT_SECONDS)
                response = self._session.get(url, params=params or {}, timeout=self.timeout)
                if response.status_code == 200:
                    data = response.json()
                    return data if isinstance(data, dict) else {}
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(0.5 * (2**attempt))
                    continue
                return {}
            except requests.RequestException as exc:
                LOG.debug("RxNav request failed for %s: %s", url, exc)
                time.sleep(0.5 * (2**attempt))
            except ValueError:
                return {}
        return {}

    def _find_rxcui(self, drug_name: str) -> str | None:
        for params in ({"name": drug_name}, {"name": drug_name, "search": "1"}):
            data = self._request_json("rxcui.json", params)
            ids = ((data.get("idGroup") or {}).get("rxnormId") or [])
            if ids:
                return str(ids[0])
        return None

    def _properties(self, rxcui: str) -> dict[str, Any]:
        data = self._request_json(f"rxcui/{rxcui}/properties.json")
        props = data.get("properties") or {}
        return props if isinstance(props, dict) else {}

    def _related_concepts(self, rxcui: str, *, tty: str) -> list[dict[str, str]]:
        data = self._request_json(f"rxcui/{rxcui}/related.json", {"tty": tty})
        groups = ((data.get("relatedGroup") or {}).get("conceptGroup") or [])
        concepts: list[dict[str, str]] = []
        for group in groups:
            for item in group.get("conceptProperties") or []:
                if not isinstance(item, dict):
                    continue
                value = {
                    "rxcui": str(item.get("rxcui") or ""),
                    "name": str(item.get("name") or ""),
                    "tty": str(item.get("tty") or ""),
                }
                if value["rxcui"] and value["name"]:
                    concepts.append(value)
        return _dedupe_records(concepts, keys=("rxcui", "name", "tty"))[:16]

    def _classes(self, rxcui: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for source in ("ATC", "MEDRT"):
            data = self._request_json(
                "rxclass/class/byRxcui.json",
                {"rxcui": rxcui, "relaSource": source},
            )
            rows = ((data.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cls = row.get("rxclassMinConceptItem") or {}
                if not isinstance(cls, dict):
                    continue
                record = {
                    "class_id": str(cls.get("classId") or ""),
                    "class_name": str(cls.get("className") or ""),
                    "class_type": str(cls.get("classType") or ""),
                    "relation": str(row.get("rela") or ""),
                    "source": source,
                }
                if record["class_name"]:
                    out.append(record)
        return _dedupe_records(out, keys=("class_id", "class_name", "class_type", "relation", "source"))[:24]


def _dedupe_records(rows: list[dict[str, str]], *, keys: tuple[str, ...]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        identity = tuple(row.get(key, "") for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(row)
    return out
