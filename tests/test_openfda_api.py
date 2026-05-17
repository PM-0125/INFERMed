import pytest
from pathlib import Path
import plotly.graph_objs as go

from src.retrieval import openfda_api
from src.retrieval.openfda_api import OpenFDAClient, FaersQuery

CACHE_SUBDIR = "openfda"

@pytest.fixture(autouse=True)
def clean_cache(tmp_path):
    # Create a fresh cache directory for each test
    cache = tmp_path / CACHE_SUBDIR
    cache.mkdir()
    return cache

@pytest.fixture
def client(clean_cache):
    # Initialize client with temporary cache dir
    return OpenFDAClient(cache_dir=str(clean_cache))


def test_default_cache_location_is_under_data_cache():
    assert openfda_api.DEFAULT_CACHE_DIR == "data/cache/openfda"


def test_legacy_openfda_cache_is_copied_to_new_default(tmp_path, monkeypatch):
    legacy = tmp_path / "data" / "openfda"
    default = tmp_path / "data" / "cache" / "openfda"
    legacy.mkdir(parents=True)
    old_file = legacy / "v2__aspirin__reactions__exact.json"
    old_file.write_text('{"headache": 2}', encoding="utf-8")

    monkeypatch.setattr(openfda_api, "DEFAULT_CACHE_DIR", str(default), raising=True)
    monkeypatch.setattr(openfda_api, "LEGACY_CACHE_DIR", str(legacy), raising=True)

    client = OpenFDAClient(cache_dir=str(default))

    migrated_file = Path(client.cache_dir) / old_file.name
    assert migrated_file.exists()
    assert migrated_file.read_text(encoding="utf-8") == '{"headache": 2}'
    assert old_file.exists()


def test_get_top_reactions_real(client):
    # Real API call for a common drug
    reactions = client.get_top_reactions('aspirin', top_k=5)
    assert isinstance(reactions, list)
    for term, count in reactions:
        assert isinstance(term, str)
        assert isinstance(count, int)
    # Optionally verify cache file created
    q = FaersQuery(drug='aspirin', count_field='patient.reaction.reactionmeddrapt.exact', suffix='reactions')
    cache_file = Path(client.cache_dir) / f"{q.cache_key}.json"
    if reactions:
        assert cache_file.exists()


def test_get_top_reactions_unknown(client):
    # Unknown drug yields empty list
    reactions = client.get_top_reactions('nonexistentdrug123', top_k=5)
    assert reactions == []


def test_fetch_openfda_summary_real(client):
    summary = client.fetch_openfda_summary('ibuprofen', limit=2)
    assert isinstance(summary, str)
    assert summary.startswith('FDA report') or 'No recent FDA event reports' in summary
    summary_file = Path(client.cache_dir) / f'{openfda_api.CACHE_VERSION}__ibuprofen__summary.json'
    assert summary_file.exists()


def test_fetch_openfda_summary_unknown(client):
    msg = client.fetch_openfda_summary('gibberishdrugxyz', limit=1)
    assert msg.startswith('No recent FDA event reports found for gibberishdrugxyz.')
    summary_file = Path(client.cache_dir) / f'{openfda_api.CACHE_VERSION}__gibberishdrugxyz__summary.json'
    assert summary_file.exists()


def test_plot_helpers(client):
    fig1 = client.plot_top_reactions('aspirin', top_k=3)
    assert isinstance(fig1, go.Figure)
    fig2 = client.plot_time_series('aspirin')
    assert isinstance(fig2, go.Figure)
    fig3 = client.plot_age_distribution('aspirin', bins=[18, 35, 65])
    assert isinstance(fig3, go.Figure)
    fig4 = client.plot_reporter_breakdown('aspirin')
    assert isinstance(fig4, go.Figure)
