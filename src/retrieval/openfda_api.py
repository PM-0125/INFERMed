# src/retrieval/openfda_api.py
# -*- coding: utf-8 -*-

import os
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from collections import Counter

import requests
import pandas as pd
import plotly.express as px

# Our atomic cache helpers (support ttl=seconds)
from src.utils.caching import load_json, save_json, load_text, save_text

# ----------------------------------------------------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------------------------------------------------

DEFAULT_CACHE_DIR = "data/openfda"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days
DEFAULT_TIMEOUT = 8  # seconds
CACHE_VERSION = "v2"  # bump to invalidate old cache keys after logic changes


# ----------------------------------------------------------------------------------------------------------------------
# Data containers
# ----------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FaersQuery:
    """
    Represents a query to the FAERS (FDA Adverse Event Reporting System) API.
    """
    drug: str
    count_field: str
    search_filters: Optional[str] = None
    suffix: Optional[str] = None

    @property
    def cache_key(self) -> str:
        """
        Generate a unique, stable cache key.
        Format: <CACHE_VERSION>__<drug_lower>__<suffix?>__<count_field_leaf>
        """
        parts = [CACHE_VERSION, self.drug.lower()]
        if self.suffix:
            parts.append(self.suffix.lower())
        parts.append(self.count_field.split(".")[-1].lower())
        return "__".join(parts)


@dataclass
class FaersData:
    """
    Stores FAERS data for a drug, including counts of reactions or other fields.
    """
    drug: str
    suffix: Optional[str]
    counts: Counter = field(default_factory=Counter)

    @property
    def total_reports(self) -> int:
        return sum(self.counts.values())

    def top_k(self, k: int = 5) -> List[Tuple[str, int]]:
        return self.counts.most_common(k)


# ----------------------------------------------------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------------------------------------------------

class OpenFDAClient:
    """
    Client for querying the OpenFDA drug event API and caching results locally.
    Public API mirrors your previous methods, with safer networking + TTL cache.
    """
    BASE_URL = "https://api.fda.gov/drug/event.json"
    SUMMARY_LIMIT = 3

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        ttl_days= os.getenv("OPENFDA_TTL_DAYS")
        if ttl_days is not None:
            try:
                ttl_seconds = int(float(ttl_days)) * 24 * 3600
            except Exception:
                pass
        self.ttl_seconds = int(ttl_seconds)
        self.api_key = os.getenv("OPENFDA_API_KEY")  # optional, but recommended

        # one session for connection pooling
        self._session = requests.Session()

    # ------------------------ internal HTTP ------------------------

    def _request(self, params: Dict[str, str], timeout: int = DEFAULT_TIMEOUT) -> Optional[Dict]:
        """
        Do a GET with polite retries for 429/5xx. Returns JSON dict or None.
        """
        # Attach API key if present
        if self.api_key:
            params = dict(params)  # shallow copy
            params["api_key"] = self.api_key

        # basic retry/backoff
        for attempt in range(4):
            try:
                resp = self._session.get(self.BASE_URL, params=params, timeout=timeout)
            except requests.RequestException:
                # transient network error; small backoff and retry
                time.sleep(0.5 * (2 ** attempt))
                continue

            code = resp.status_code
            if code == 200:
                try:
                    return resp.json()
                except ValueError:
                    return None

            if code in (429, 500, 502, 503, 504):
                # exponential backoff
                time.sleep(0.75 * (2 ** attempt))
                continue

            # Other non-retryable codes
            return None

        return None

    # ------------------------ caching wrapper ------------------------

    def _fetch_and_cache_counts(self, query: FaersQuery) -> Dict[str, int]:
        """
        Count endpoint wrapper with TTL caching.
        """
        key = query.cache_key

        # cache first
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        # Build search
        if query.search_filters:
            search = query.search_filters  # caller provided a full query string
        else:
            # default single-drug search (prefer exact for specificity)
            # openFDA stores medicinalproduct UPPERCASE; exact match benefits from .exact
            search = f"patient.drug.medicinalproduct.exact:{query.drug.upper()}"

        params = {
            "search": search,
            "count": query.count_field,
        }

        payload = self._request(params)
        mapping: Dict[str, int] = {}
        if payload and isinstance(payload, dict):
            for item in payload.get("results", []) or []:
                k = item.get("term") or item.get("time")
                if k is None:
                    continue
                mapping[str(k)] = int(item.get("count", 0))

        # atomic write (even if empty, so we avoid hammering)
        save_json(self.cache_dir, key, mapping)
        return mapping

    # alias for backward compatibility with older code
    _fetch_and_cache = _fetch_and_cache_counts

    # ------------------------ public methods ------------------------

    def fetch_openfda_summary(self, drug_name: str, limit: Optional[int] = None) -> str:
        """
        Fetch N recent reports and build a short textual summary. Cached with TTL.
        """
        lim = int(limit or self.SUMMARY_LIMIT)
        key = f"{CACHE_VERSION}__{drug_name.lower()}__summary"

        cached = load_text(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        def _retrieve(exact: bool) -> List[Dict]:
            search = (
                f"patient.drug.medicinalproduct.exact:{drug_name.upper()}"
                if exact
                else f"patient.drug.medicinalproduct:{drug_name}"
            )
            params = {"limit": str(lim), "search": search}
            payload = self._request(params)
            if not payload or not isinstance(payload, dict):
                return []
            return payload.get("results", []) or []

        results = _retrieve(True) or _retrieve(False)
        if not results:
            summary = f"No recent FDA event reports found for {drug_name}."
            save_text(self.cache_dir, key, summary)  # cache negative too
            return summary

        lines: List[str] = []
        for idx, entry in enumerate(results, start=1):
            effects = (entry.get("patient") or {}).get("reaction", []) or []
            terms = [e.get("reactionmeddrapt", "Unknown") for e in effects if isinstance(e, dict)]
            if terms:
                lines.append(
                    f"FDA report #{idx}: Common adverse events include {', '.join(terms[:5])}."
                )

        summary = "\n".join(lines) if lines else f"{lim} FDA reports retrieved for {drug_name}."
        save_text(self.cache_dir, key, summary)
        return summary

    def get_top_reactions(self, drug: str, top_k: int = 5) -> List[Tuple[str, int]]:
        """
        Top reactions for a single drug (PRR-like frequency proxy).
        """
        q = FaersQuery(drug=drug, count_field="patient.reaction.reactionmeddrapt.exact", suffix="reactions")
        data = Counter(self._fetch_and_cache_counts(q))
        return data.most_common(int(top_k))

    def get_time_series(self, drug: str, interval: str = "receivedate") -> List[Tuple[str, int]]:
        """
        Time series of counts (count=<interval>), sorted by date string.
        """
        q = FaersQuery(drug=drug, count_field=interval, suffix="time")
        data = self._fetch_and_cache_counts(q)
        return sorted(data.items(), key=lambda x: x[0])

    def get_age_distribution(self, drug: str, bins: Optional[List[int]] = None) -> Dict[str, int]:
        """
        Age distribution. If bins provided, bucketize raw ages.
        """
        q = FaersQuery(drug=drug, count_field="patient.patientonsetage.exact", suffix="age")
        raw = self._fetch_and_cache_counts(q)
        if not bins:
            return raw
        buckets: Dict[str, int] = {}
        for k, v in raw.items():
            try:
                age = int(k)
            except (TypeError, ValueError):
                continue
            for b in bins:
                if age <= b:
                    label = f"<= {b}"
                    buckets[label] = buckets.get(label, 0) + v
                    break
        return buckets

    def get_reporter_breakdown(self, drug: str) -> Dict[str, int]:
        """
        Reporter roles, e.g., physician, consumer, etc.
        """
        q = FaersQuery(drug=drug, count_field="primarysource.qualification.exact", suffix="reporter")
        return self._fetch_and_cache_counts(q)

    def get_combination_reactions(self, drug1: str, drug2: str, top_k: int = 5) -> List[Tuple[str, int]]:
        """
        Top reactions for a combination. Tries a true combo filter first; if empty, returns intersection of singles.
        """
        # combo search (verbatim; use .exact for precision)
        search = (
            f"patient.drug.medicinalproduct.exact:{drug1.upper()}+AND+"
            f"patient.drug.medicinalproduct.exact:{drug2.upper()}"
        )
        q = FaersQuery(
            drug=f"{drug1}_{drug2}",
            count_field="patient.reaction.reactionmeddrapt.exact",
            search_filters=search,
            suffix="combo",
        )
        data = self._fetch_and_cache_counts(q)
        if data:
            return Counter(data).most_common(int(top_k))

        # fallback: intersection of top reactions from each single
        c1 = Counter(self._fetch_and_cache_counts(FaersQuery(drug1, "patient.reaction.reactionmeddrapt.exact")))
        c2 = Counter(self._fetch_and_cache_counts(FaersQuery(drug2, "patient.reaction.reactionmeddrapt.exact")))
        return (c1 & c2).most_common(int(top_k))

    # ------------------------ plotting helpers (unchanged API) ------------------------

    def plot_top_reactions(self, drug: str, top_k: int = 5):
        data = self.get_top_reactions(drug, top_k)
        df = pd.DataFrame(data, columns=["reaction", "count"])
        return px.bar(df, x="reaction", y="count", title=f"Top {top_k} Reactions for {drug.title()}")

    def plot_time_series(self, drug: str, interval: str = "receivedate"):
        data = self.get_time_series(drug, interval)
        df = pd.DataFrame(data, columns=["date", "count"])
        return px.line(df, x="date", y="count", title=f"Event Count over Time for {drug.title()}")

    def plot_age_distribution(self, drug: str, bins: Optional[List[int]] = None):
        dist = self.get_age_distribution(drug, bins)
        df = pd.DataFrame(list(dist.items()), columns=["age_bin", "count"])
        return px.bar(df, x="age_bin", y="count", title=f"Age Distribution for {drug.title()}")

    def plot_reporter_breakdown(self, drug: str):
        data = self.get_reporter_breakdown(drug)
        df = pd.DataFrame(list(data.items()), columns=["reporter", "count"])
        return px.pie(df, names="reporter", values="count", title=f"Reporter Breakdown for {drug.title()}")
