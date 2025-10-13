import os
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

import requests
from collections import Counter
import pandas as pd
import plotly.express as px
# add at top with other imports
from src.utils.caching import load_json, save_json, load_text, save_text


@dataclass(frozen=True)
class FaersQuery:
    """
    Represents a query to the FAERS (FDA Adverse Event Reporting System) API.
    Attributes:
        drug: Drug name to query.
        count_field: Field to count/group by in the API response.
        search_filters: Optional additional search filters for the API.
        suffix: Optional suffix for cache key uniqueness.
    """
    drug: str
    count_field: str
    search_filters: Optional[str] = None
    suffix: Optional[str] = None

    @property
    def cache_key(self) -> str:
        """Generate a unique cache key for the query."""
        parts = [self.drug.lower()]
        if self.suffix:
            parts.append(self.suffix.lower())
        parts.append(self.count_field.split('.')[-1].lower())
        return '_'.join(parts)


@dataclass
class FaersData:
    """
    Stores FAERS data for a drug, including counts of reactions or other fields.
    Attributes:
        drug: Drug name.
        suffix: Suffix for context (e.g., 'reactions', 'age').
        counts: Counter of term -> count.
    """
    drug: str
    suffix: Optional[str]
    counts: Counter = field(default_factory=Counter)

    @property
    def total_reports(self) -> int:
        """Return the total number of reports for the drug."""
        return sum(self.counts.values())

    def top_k(self, k: int = 5) -> List[Tuple[str, int]]:
        """Return the top k most common terms and their counts."""
        return self.counts.most_common(k)


class OpenFDAClient:
    """
    Client for querying the OpenFDA drug event API and caching results locally.
    Provides methods for retrieving and visualizing drug adverse event data.
    """
    BASE_URL = "https://api.fda.gov/drug/event.json"
    SUMMARY_LIMIT = 3

    def __init__(self, cache_dir: str = "/home/pranjul/mydata/INFERMed/data/openfda"):
        """
        Initialize the client and create the cache directory if it doesn't exist.
        Args:
            cache_dir: Directory to store cached API responses.
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_and_cache_counts(self, query: FaersQuery) -> Dict[str, int]:
        # cache key (filename without extension) remains identical
        key = query.cache_key

        # try cache
        cached = load_json(self.cache_dir, key)
        if cached is not None:
            return cached

        params = {'search': f"patient.drug.medicinalproduct:{query.drug.upper()}"}
        if query.search_filters:
            params['search'] = query.search_filters
        params['count'] = query.count_field

        for attempt in range(3):
            resp = requests.get(self.BASE_URL, params=params)
            if resp.status_code == 200:
                payload = resp.json()
                results = payload.get('results', []) or []
                mapping: Dict[str, int] = {}
                for item in results:
                    k = item.get('term') or item.get('time')
                    if k is None:
                        continue
                    mapping[k] = item.get('count', 0)

                # atomic write via utils
                save_json(self.cache_dir, key, mapping)
                return mapping

            elif resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            else:
                break

        return {}

    # alias for backward compatibility
    _fetch_and_cache = _fetch_and_cache_counts

    def fetch_openfda_summary(self, drug_name: str, limit: Optional[int] = None) -> str:
        lim = limit or self.SUMMARY_LIMIT
        key = f"{drug_name.lower()}_summary"  # keep the filename: <drug>_summary.json

        # load text summary if cached
        cached = load_text(self.cache_dir, key)
        if cached is not None:
            return cached

        def _retrieve(exact: bool) -> List[Dict]:
            params = {
                'limit': lim,
                'search': (
                    f"patient.drug.medicinalproduct.exact:{drug_name}" if exact
                    else f"patient.drug.medicinalproduct:{drug_name}"
                )
            }
            try:
                r = requests.get(self.BASE_URL, params=params, timeout=5)
                if r.status_code != 200:
                    return []
                return r.json().get('results', []) or []
            except Exception:
                return []

        results = _retrieve(True)
        if not results:
            results = _retrieve(False)
        if not results:
            return f"No recent FDA event reports found for {drug_name}."

        lines: List[str] = []
        for idx, entry in enumerate(results, start=1):
            effects = entry.get('patient', {}).get('reaction', []) or []
            terms = [e.get('reactionmeddrapt', 'Unknown') for e in effects]
            if terms:
                lines.append(f"FDA report #{idx}: Common adverse events include {', '.join(terms[:5])}.")

        summary = "\n".join(lines)

        # atomic write via utils (still writes to .../<drug>_summary.json)
        save_text(self.cache_dir, key, summary)
        return summary

    def get_top_reactions(self, drug: str, top_k: int = 5) -> List[Tuple[str, int]]:
        """
        Get the top k most common adverse reactions for a drug.
        Args:
            drug: Drug name.
            top_k: Number of top reactions to return.
        Returns:
            List of (reaction, count) tuples.
        """
        q = FaersQuery(drug, 'patient.reaction.reactionmeddrapt.exact', suffix='reactions')
        data = Counter(self._fetch_and_cache_counts(q))
        return data.most_common(top_k)

    def get_time_series(self, drug: str, interval: str = 'receivedate') -> List[Tuple[str, int]]:
        """
        Get time series data for a drug, grouped by interval (e.g., receivedate).
        Args:
            drug: Drug name.
            interval: Field to group by (default: receivedate).
        Returns:
            List of (date, count) tuples sorted by date.
        """
        q = FaersQuery(drug, interval, suffix='time')
        data = self._fetch_and_cache_counts(q)
        return sorted(data.items(), key=lambda x: x[0])

    def get_age_distribution(self, drug: str, bins: Optional[List[int]] = None) -> Dict[str, int]:
        """
        Get age distribution for a drug's adverse event reports.
        Args:
            drug: Drug name.
            bins: Optional list of age bins for bucketing.
        Returns:
            Dictionary of age bin (or age) to count.
        """
        q = FaersQuery(drug, 'patient.patientonsetage.exact', suffix='age')
        raw = self._fetch_and_cache_counts(q)
        if not bins:
            return raw
        buckets: Dict[str, int] = {}
        for k, v in raw.items():
            try:
                age = int(k)
            except ValueError:
                continue
            for b in bins:
                if age <= b:
                    label = f"<= {b}"
                    buckets[label] = buckets.get(label, 0) + v
                    break
        return buckets

    def get_reporter_breakdown(self, drug: str) -> Dict[str, int]:
        """
        Get breakdown of report sources (e.g., physician, consumer) for a drug.
        Args:
            drug: Drug name.
        Returns:
            Dictionary of reporter type to count.
        """
        q = FaersQuery(drug, 'primarysource.qualification.exact', suffix='reporter')
        return self._fetch_and_cache_counts(q)

    def get_combination_reactions(self, drug1: str, drug2: str, top_k: int = 5) -> List[Tuple[str, int]]:
        """
        Get top reactions reported for a combination of two drugs.
        Args:
            drug1: First drug name.
            drug2: Second drug name.
            top_k: Number of top reactions to return.
        Returns:
            List of (reaction, count) tuples.
        """
        search = (
            f"patient.drug.medicinalproduct:{drug1.upper()}+AND+"
            f"patient.drug.medicinalproduct:{drug2.upper()}"
        )
        q = FaersQuery(f"{drug1}_{drug2}", 'patient.reaction.reactionmeddrapt.exact', search_filters=search, suffix='combo')
        data = self._fetch_and_cache_counts(q)
        if data:
            return Counter(data).most_common(top_k)
        c1 = Counter(self._fetch_and_cache_counts(FaersQuery(drug1, 'patient.reaction.reactionmeddrapt.exact')))
        c2 = Counter(self._fetch_and_cache_counts(FaersQuery(drug2, 'patient.reaction.reactionmeddrapt.exact')))
        return (c1 & c2).most_common(top_k)

    # Plotting helpers
    def plot_top_reactions(self, drug: str, top_k: int = 5):
        """
        Plot a bar chart of the top k reactions for a drug.
        Args:
            drug: Drug name.
            top_k: Number of top reactions to plot.
        Returns:
            Plotly Figure object.
        """
        data = self.get_top_reactions(drug, top_k)
        df = pd.DataFrame(data, columns=['reaction', 'count'])
        return px.bar(df, x='reaction', y='count', title=f"Top {top_k} Reactions for {drug.title()}")

    def plot_time_series(self, drug: str, interval: str = 'receivedate'):
        """
        Plot a time series of event counts for a drug.
        Args:
            drug: Drug name.
            interval: Field to group by (default: receivedate).
        Returns:
            Plotly Figure object.
        """
        data = self.get_time_series(drug, interval)
        df = pd.DataFrame(data, columns=['date', 'count'])
        return px.line(df, x='date', y='count', title=f"Event Count over Time for {drug.title()}")

    def plot_age_distribution(self, drug: str, bins: Optional[List[int]] = None):
        """
        Plot a bar chart of age distribution for a drug's adverse event reports.
        Args:
            drug: Drug name.
            bins: Optional list of age bins for bucketing.
        Returns:
            Plotly Figure object.
        """
        dist = self.get_age_distribution(drug, bins)
        df = pd.DataFrame(list(dist.items()), columns=['age_bin', 'count'])
        return px.bar(df, x='age_bin', y='count', title=f"Age Distribution for {drug.title()}")

    def plot_reporter_breakdown(self, drug: str):
        """
        Plot a pie chart of reporter breakdown for a drug.
        Args:
            drug: Drug name.
        Returns:
            Plotly Figure object.
        """
        data = self.get_reporter_breakdown(drug)
        df = pd.DataFrame(list(data.items()), columns=['reporter', 'count'])
        return px.pie(df, names='reporter', values='count', title=f"Reporter Breakdown for {drug.title()}")