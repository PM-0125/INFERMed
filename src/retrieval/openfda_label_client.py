from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

from src.utils.caching import load_json, save_json

LOG = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "data/cache/openfda_label"
DEFAULT_TTL_SECONDS = 30 * 24 * 3600
DEFAULT_TIMEOUT = 10
BASE_URL = "https://api.fda.gov/drug/label.json"

SECTION_FIELDS = (
    "boxed_warning",
    "contraindications",
    "warnings",
    "warnings_and_cautions",
    "drug_interactions",
    "clinical_pharmacology",
    "pharmacokinetics",
    "pharmacodynamics",
    "adverse_reactions",
)


class OpenFDALabelClient:
    """Retrieve normalized public drug-label sections from openFDA."""

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
        self.api_key = os.getenv("OPENFDA_API_KEY")
        self._session = requests.Session()

    def get_label(self, drug_name: str) -> dict[str, Any]:
        name = str(drug_name or "").strip()
        if not name:
            return {"query": drug_name, "found": False, "sections": {}}

        key = f"openfda_label__{name}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        raw = self._fetch_first(name)
        payload = self._normalize(name, raw)
        save_json(self.cache_dir, key, payload)
        return payload

    def _fetch_first(self, drug_name: str) -> dict[str, Any] | None:
        escaped = drug_name.upper().replace('"', '\\"')
        queries = [
            f'openfda.generic_name.exact:"{escaped}"',
            f'openfda.substance_name.exact:"{escaped}"',
            f'openfda.brand_name.exact:"{escaped}"',
            f'openfda.generic_name:{drug_name}',
            f'openfda.substance_name:{drug_name}',
        ]
        for query in queries:
            payload = self._request({"search": query, "limit": "1"})
            results = (payload or {}).get("results") or []
            if results:
                first = results[0]
                return first if isinstance(first, dict) else None
        return None

    def _request(self, params: dict[str, str]) -> dict[str, Any] | None:
        if self.api_key:
            params = dict(params)
            params["api_key"] = self.api_key
        for attempt in range(3):
            try:
                response = self._session.get(BASE_URL, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    data = response.json()
                    return data if isinstance(data, dict) else None
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(0.5 * (2**attempt))
                    continue
                return None
            except requests.RequestException as exc:
                LOG.debug("openFDA label request failed: %s", exc)
                time.sleep(0.5 * (2**attempt))
            except ValueError:
                return None
        return None

    def _normalize(self, drug_name: str, raw: dict[str, Any] | None) -> dict[str, Any]:
        if not raw:
            return {
                "query": drug_name,
                "found": False,
                "sections": {},
                "provenance": {
                    "source": "openFDA Drug Label API",
                    "source_url": "https://open.fda.gov/apis/drug/label/",
                },
            }

        openfda = raw.get("openfda") or {}
        sections: dict[str, str] = {}
        for field in SECTION_FIELDS:
            text = _collapse_section(raw.get(field))
            if text:
                sections[field] = text

        return {
            "query": drug_name,
            "found": True,
            "label_id": raw.get("id"),
            "set_id": raw.get("set_id"),
            "effective_time": raw.get("effective_time"),
            "brand_names": _string_list(openfda.get("brand_name")),
            "generic_names": _string_list(openfda.get("generic_name")),
            "substance_names": _string_list(openfda.get("substance_name")),
            "manufacturer_names": _string_list(openfda.get("manufacturer_name")),
            "rxcui": _string_list(openfda.get("rxcui")),
            "sections": sections,
            "provenance": {
                "source": "openFDA Drug Label API",
                "source_url": "https://open.fda.gov/apis/drug/label/",
                "api_endpoint": BASE_URL,
            },
            "limitations": [
                "Label content is product/version specific and may not match every marketed product.",
                "openFDA reformats submitted SPL labeling and provides public API access with responsible-use caveats.",
            ],
        }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _collapse_section(value: Any, *, max_chars: int = 1400) -> str:
    parts = _string_list(value)
    text = " ".join(parts)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "..."
    return text
