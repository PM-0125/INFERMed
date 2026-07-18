from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests

from src.utils.caching import load_json, save_json

LOG = logging.getLogger(__name__)

DAILYMED_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
DEFAULT_CACHE_DIR = "data/cache/dailymed"
DEFAULT_TTL_SECONDS = 30 * 24 * 3600
DEFAULT_TIMEOUT = 10


class DailyMedClient:
    """DailyMed v2 web-service metadata wrapper for current SPL records."""

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

    def get_spl_metadata(self, drug_name: str) -> dict[str, Any]:
        name = str(drug_name or "").strip()
        if not name:
            return {"query": drug_name, "found": False, "records": []}

        key = f"dailymed__spls__{name}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        payload = self._request(
            "spls.json",
            {
                "drug_name": name,
                "pagesize": "5",
                "page": "1",
            },
        )
        records = self._normalize_records(payload)
        out = {
            "query": name,
            "found": bool(records),
            "records": records,
            "provenance": {
                "source": "DailyMed SPL Web Services",
                "source_url": "https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm",
                "api_base": DAILYMED_BASE,
            },
        }
        save_json(self.cache_dir, key, out)
        return out

    def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{DAILYMED_BASE}/{path.lstrip('/')}"
        for attempt in range(3):
            try:
                time.sleep(0.15)
                response = self._session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    data = response.json()
                    return data if isinstance(data, dict) else {}
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(0.5 * (2**attempt))
                    continue
                return {}
            except requests.RequestException as exc:
                LOG.debug("DailyMed request failed for %s: %s", url, exc)
                time.sleep(0.5 * (2**attempt))
            except ValueError:
                return {}
        return {}

    @staticmethod
    def _normalize_records(payload: dict[str, Any]) -> list[dict[str, str]]:
        rows = (((payload or {}).get("data") or []) if isinstance(payload, dict) else [])
        out: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            setid = str(row.get("setid") or row.get("setId") or "").strip()
            title = str(row.get("title") or "").strip()
            if not setid and not title:
                continue
            out.append(
                {
                    "set_id": setid,
                    "title": title,
                    "spl_version": str(row.get("spl_version") or row.get("splVersion") or "").strip(),
                    "published_date": str(row.get("published_date") or row.get("publishedDate") or "").strip(),
                    "source_url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}" if setid else "",
                }
            )
        return out[:5]
