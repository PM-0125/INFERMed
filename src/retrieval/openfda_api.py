import os
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

import requests
from collections import Counter
import plotly.express as px


@dataclass(frozen=True)
class FaersQuery:
    drug: str
    count_field: str
    search_filters: Optional[str] = None
    suffix: Optional[str] = None

    @property
    def cache_key(self) -> str:
        parts = [self.drug.lower()]
        if self.suffix:
            parts.append(self.suffix.lower())
        parts.append(self.count_field.split('.')[-1].lower())
        return '_'.join(parts)


@dataclass
class FaersData:
    drug: str
    suffix: Optional[str]
    counts: Counter = field(default_factory=Counter)

    @property
    def total_reports(self) -> int:
        return sum(self.counts.values())

    def top_k(self, k: int = 5) -> List[Tuple[str, int]]:
        return self.counts.most_common(k)


class OpenFDAClient:
    BASE_URL = "https://api.fda.gov/drug/event.json"

    def __init__(self, cache_dir: str = "data/openfda"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_and_cache(self, query: FaersQuery) -> Dict[str, int]:
        cache_file = self.cache_dir / f"{query.cache_key}.json"
        if cache_file.exists():
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        params = {
            'search': f"patient.drug.medicinalproduct:{query.drug.upper()}"
        }
        if query.search_filters:
            params['search'] = query.search_filters
        params['count'] = query.count_field

        for attempt in range(3):
            response = requests.get(self.BASE_URL, params=params)
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                # dynamic key for term or time
                mapping: Dict[str, int] = {}
                for item in results:
                    key = item.get('term') or item.get('time')
                    if key is None:
                        # skip if no recognizable key
                        continue
                    mapping[key] = item.get('count', 0)
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(mapping, f)
                return mapping
            elif response.status_code == 429:
                time.sleep(2 ** attempt)
            else:
                break
        return {}

    def get_top_reactions(self, drug: str, top_k: int = 5) -> List[Tuple[str, int]]:
        q = FaersQuery(drug=drug, count_field="patient.reaction.reactionmeddrapt.exact", suffix="reactions")
        data = self._fetch_and_cache(q)
        fd = FaersData(drug, q.suffix, Counter(data))
        return fd.top_k(top_k)

    def get_time_series(self, drug: str, interval: str = "receivedate") -> List[Tuple[str, int]]:
        q = FaersQuery(drug=drug, count_field=interval, suffix="time")
        data = self._fetch_and_cache(q)
        sorted_items = sorted(data.items(), key=lambda x: x[0])
        return sorted_items

    def get_age_distribution(self, drug: str, bins: Optional[List[int]] = None) -> Dict[str, int]:
        q = FaersQuery(drug=drug, count_field="patient.patientonsetage.exact", suffix="age")
        raw = self._fetch_and_cache(q)
        if not bins:
            return raw
        buckets: Dict[str, int] = {}
        for age_str, count in raw.items():
            try:
                age = int(age_str)
            except ValueError:
                continue
            for b in bins:
                if age <= b:
                    key = f"<= {b}"
                    buckets[key] = buckets.get(key, 0) + count
                    break
        return buckets

    def get_reporter_breakdown(self, drug: str) -> Dict[str, int]:
        q = FaersQuery(drug=drug, count_field="primarysource.qualification.exact", suffix="reporter")
        return self._fetch_and_cache(q)

    def get_combination_reactions(self, drug1: str, drug2: str, top_k: int = 5) -> List[Tuple[str, int]]:
        search = (
            f"patient.drug.medicinalproduct:{drug1.upper()}+AND+"
            f"patient.drug.medicinalproduct:{drug2.upper()}"
        )
        q = FaersQuery(drug=f"{drug1}_{drug2}", count_field="patient.reaction.reactionmeddrapt.exact",
                       search_filters=search, suffix="combo")
        data = self._fetch_and_cache(q)
        if data:
            return Counter(data).most_common(top_k)
        c1 = Counter(self._fetch_and_cache(FaersQuery(drug1, "patient.reaction.reactionmeddrapt.exact")))
        c2 = Counter(self._fetch_and_cache(FaersQuery(drug2, "patient.reaction.reactionmeddrapt.exact")))
        shared = c1 & c2
        return shared.most_common(top_k)

    # Plotting helpers for frontend
    def plot_top_reactions(self, drug: str, top_k: int = 5):
        data = self.get_top_reactions(drug, top_k)
        df = {'reaction': [x[0] for x in data], 'count': [x[1] for x in data]}
        fig = px.bar(df, x='reaction', y='count', title=f"Top {top_k} Reactions for {drug.title()}")
        return fig

    def plot_time_series(self, drug: str, interval: str = "receivedate"):
        data = self.get_time_series(drug, interval)
        df = {'date': [x[0] for x in data], 'count': [x[1] for x in data]}
        fig = px.line(df, x='date', y='count', title=f"Event Count over Time for {drug.title()}")
        return fig

    def plot_age_distribution(self, drug: str, bins: Optional[List[int]] = None):
        dist = self.get_age_distribution(drug, bins)
        df = {'age_bin': list(dist.keys()), 'count': list(dist.values())}
        fig = px.bar(df, x='age_bin', y='count', title=f"Age Distribution for {drug.title()}")
        return fig

    def plot_reporter_breakdown(self, drug: str):
        data = self.get_reporter_breakdown(drug)
        df = {'reporter': list(data.keys()), 'count': list(data.values())}
        fig = px.pie(df, names='reporter', values='count', title=f"Reporter Breakdown for {drug.title()}")
        return fig