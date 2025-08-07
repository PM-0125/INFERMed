import os
import json
import pytest
from pathlib import Path
from collections import Counter

import plotly.graph_objs as go

from src.retrieval.openfda_api import OpenFDAClient, FaersQuery, FaersData


class DummyResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


def dummy_get(url, params=None):
    """
    Dummy requests.get replacement that returns predictable data based on params['count'].
    """
    count_field = params.get('count')
    term = count_field.split('.')[-1]
    results = [
        {'term': f"{term}_A", 'count': 10},
        {'term': f"{term}_B", 'count': 5},
    ]
    return DummyResponse(200, {'results': results})


def test_fetch_and_cache_and_load(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    client = OpenFDAClient(cache_dir=str(cache_dir))
    # monkeypatch requests.get
    monkeypatch.setattr('src.retrieval.openfda_api.requests.get', dummy_get)

    q = FaersQuery(drug='testdrug', count_field='patient.reaction.reactionmeddrapt.exact', suffix='reactions')
    data = client._fetch_and_cache(q)
    assert isinstance(data, dict)
    last = q.count_field.split('.')[-1]
    assert f"{last}_A" in data and f"{last}_B" in data
    cache_file = cache_dir / f"{q.cache_key}.json"
    assert cache_file.exists()

    # Modify cache to known content
    with open(cache_file, 'w') as f:
        json.dump({'X': 1}, f)
    loaded = client._fetch_and_cache(q)
    assert loaded == {'X': 1}


def test_get_top_reactions(monkeypatch, tmp_path):
    client = OpenFDAClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client, '_fetch_and_cache', lambda q: {'nausea': 8, 'headache': 3, 'dizziness': 5})
    top2 = client.get_top_reactions('aspirin', top_k=2)
    assert top2 == [('nausea', 8), ('dizziness', 5)]


def test_get_time_series(monkeypatch, tmp_path):
    client = OpenFDAClient(cache_dir=str(tmp_path))
    fake = {'20200101': 7, '20200301': 2, '20191231': 5}
    monkeypatch.setattr(client, '_fetch_and_cache', lambda q: fake)
    series = client.get_time_series('metformin')
    assert series == [('20191231', 5), ('20200101', 7), ('20200301', 2)]


def test_get_age_distribution(monkeypatch, tmp_path):
    client = OpenFDAClient(cache_dir=str(tmp_path))
    raw = {'10': 4, '20': 6, 'abc': 3, '30': 2}
    monkeypatch.setattr(client, '_fetch_and_cache', lambda q: raw)
    dist = client.get_age_distribution('ibuprofen', bins=[15, 25, 35])
    assert dist == {'<= 15': 4, '<= 25': 6, '<= 35': 2}


def test_reporter_breakdown(monkeypatch, tmp_path):
    client = OpenFDAClient(cache_dir=str(tmp_path))
    raw = {'Physician': 12, 'Consumer': 5}
    monkeypatch.setattr(client, '_fetch_and_cache', lambda q: raw)
    breakdown = client.get_reporter_breakdown('aspirin')
    assert breakdown == raw


def test_combination_reactions_direct(monkeypatch, tmp_path):
    client = OpenFDAClient(cache_dir=str(tmp_path))
    direct = {'A': 4, 'B': 2}
    def fetch(q):
        return direct if q.suffix == 'combo' else {}
    monkeypatch.setattr(client, '_fetch_and_cache', fetch)
    combo = client.get_combination_reactions('a', 'b', top_k=2)
    assert combo == [('A', 4), ('B', 2)]


def test_combination_reactions_fallback(monkeypatch, tmp_path):
    client = OpenFDAClient(cache_dir=str(tmp_path))
    indiv = {'x': 5, 'y': 3}
    def fetch(q):
        return {} if q.suffix == 'combo' else indiv
    monkeypatch.setattr(client, '_fetch_and_cache', fetch)
    combo = client.get_combination_reactions('a', 'b', top_k=2)
    assert combo == [('x', 5), ('y', 3)]


def test_plot_helpers(monkeypatch, tmp_path):
    client = OpenFDAClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(client, 'get_top_reactions', lambda d, k=5: [('r', 1)])
    monkeypatch.setattr(client, 'get_time_series', lambda d, i='receivedate': [('20200101', 1)])
    monkeypatch.setattr(client, 'get_age_distribution', lambda d, b=None: {'<= 10': 2})
    monkeypatch.setattr(client, 'get_reporter_breakdown', lambda d: {'A': 3, 'B': 7})

    fig1 = client.plot_top_reactions('d')
    assert isinstance(fig1, go.Figure)
    fig2 = client.plot_time_series('d')
    assert isinstance(fig2, go.Figure)
    fig3 = client.plot_age_distribution('d')
    assert isinstance(fig3, go.Figure)
    fig4 = client.plot_reporter_breakdown('d')
    assert isinstance(fig4, go.Figure)
