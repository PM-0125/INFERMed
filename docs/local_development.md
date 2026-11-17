# Local development

Run commands from the repository root unless stated otherwise. This guide does not assume access to a particular workstation, cloud host, or model service.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
npm.cmd --prefix frontend ci
```

Use an existing virtual environment when available. Keep credentials and local configuration out of Git.

## Configuration

[src/config/settings.py](../src/config/settings.py) reads two configuration sources:

- [data_manifest.yaml](../data_manifest.yaml): data mode, source switches, dataset paths, cache policy, and retrieval budgets.
- `.env` or process environment: provider credentials, model settings, endpoint addresses, and other environment-based options defined in `Settings`.

`INFERMED_DATA_CONFIG` selects an alternative YAML file. `INFERMED_SKIP_DOTENV=1` prevents loading the repository `.env`; it does not disable network requests or clear existing process variables. Settings are cached within the process, so restart the backend after configuration changes.

Data controls such as `ENABLE_DRUGBANK` and `INFERMED_DATA_MODE` are not ordinary `.env` overrides anymore. Compatibility overrides require `INFERMED_ALLOW_DATA_ENV_OVERRIDES=true`, primarily for tests. Prefer YAML for normal use.

The checked-in manifest selects `local_dev`, enables DrugBank, disables QLever, and uses SQLite caching. That configuration is not a public deployment preset. A public-safe configuration should select:

```yaml
runtime:
  data_mode: public_safe
  sources:
    drugbank: false
    qlever: false
```

This is a fragment to apply to a complete configuration, not a replacement manifest. `public_safe` forces DrugBank and QLever off in settings. Other sources remain subject to configuration and data policy. An enabled source may still be unavailable or return no evidence.

## Data and retrieval

The manifest lists dataset filenames and their access classifications. Analytical files normally live under `data/duckdb`; source caches live under `data/cache`. Missing optional datasets reduce available evidence. Do not treat a source switch as confirmation that files, credentials, or redistribution rights exist.

Use the healthcheck to inspect local readiness:

```powershell
.\.venv\Scripts\python.exe scripts\local_healthcheck.py --skip-dotenv
.\.venv\Scripts\python.exe scripts\local_healthcheck.py --data-mode local_dev --enable-drugbank --skip-dotenv
```

The healthcheck applies its own command-line mode, which defaults to `public_safe`. Its result does not change the app configuration or verify every live API.

Conversion utilities are [build_parquets.py](../scripts/build_parquets.py) and [build_drugbank_parquet.py](../scripts/build_drugbank_parquet.py). Inspect their `--help` before rebuilding data. DrugBank requires separate authorization. Keep bulk datasets and generated outputs out of Git; see [data licensing](../data_license.md).

Public API switches, including PubChem REST/PUG-View, ChEMBL, UniProt, KEGG, and Reactome, live under `runtime.sources`. QLever additionally needs configured endpoints and available indexes. Semantic search and reranking may load or download models; enable them only when their dependencies are prepared.

## Model providers

Select `LLM_PROVIDER` in the environment:

| Value | Requirements |
|---|---|
| `mock` | Deterministic placeholder generation, useful for UI and engineering checks |
| `nvidia` | Provider credentials and a supported model configured through the NVIDIA settings |
| `ollama` | A reachable Ollama endpoint with the configured model available |

Mock generation does not make evidence retrieval offline. Disable or mock external retrieval separately when an offline check is required.

For Ollama, configure `OLLAMA_HOST` and `OLLAMA_MODEL`. For NVIDIA, configure `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, and `NVIDIA_MODEL`. Model availability and hardware requirements depend on the chosen service. Do not assume a model is installed or a remote host is reachable.

[scripts/check_nvidia.py](../scripts/check_nvidia.py) provides a direct provider diagnostic; it sends a real request. Optional remote Ollama tunnelling is described in [streaming and prompt reuse](streaming_and_prompt_cache.md).

## Run the app

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Set the frontend backend URL in `frontend/.env.local`:

```dotenv
VITE_INFERMED_API_URL=http://127.0.0.1:8000
```

Then start the frontend:

```powershell
npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. Restart Vite after changing its environment. The API client requires this URL for backend requests; Vite does not configure an API proxy.

Check backend readiness with `Invoke-RestMethod http://127.0.0.1:8000/api/health`. A healthy backend does not establish successful retrieval or answer quality.

The separate Streamlit console can be started with:

```powershell
.\.venv\Scripts\python.exe -m streamlit run src/frontend/app.py
```

Its file watcher is disabled in `.streamlit/config.toml`, so restart it after code changes. Do not assume its presentation path is identical to the FastAPI/React path.

## Verification

Run focused tests for the changed behavior first. For example:

```powershell
$env:INFERMED_SKIP_DOTENV='1'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_source_integrity.py tests/test_label_structure.py -q
.\.venv\Scripts\python.exe -m black --check src tests scripts
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```

For broader changes, run `.\.venv\Scripts\python.exe -m pytest tests -q`. Some tests depend on local data, optional model packages, or live services. Inspect their setup before running them; skips are not evidence that those paths work. `--skip-dotenv` and `INFERMED_SKIP_DOTENV` are not network isolation controls.

The [quality workflow](../.github/workflows/quality.yml) runs Black, frontend lint, and the frontend build. It does not run Python tests or the frontend test command. Report local test results separately from CI status.
