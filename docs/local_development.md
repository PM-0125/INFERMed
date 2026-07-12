# Local Development Runtime

Cloud access is currently paused, so active development and testing should run on this workstation.

## Data Location

Keep generated and licensed data local. These folders are ignored by Git:

```text
data/duckdb/
data/cache/
data/openfda/
data/pubchem/
data/qlever/
```

Expected local analytical files:

```text
data/duckdb/twosides.parquet
data/duckdb/dilirank.parquet
data/duckdb/dictrank.parquet
data/duckdb/diqt.parquet
```

Optional licensed local-only file:

```text
data/duckdb/drugbank.parquet
```

DrugBank must remain disabled for public-safe mode and must not be committed.

## Healthcheck

Run a public-safe local check:

```powershell
.\.venv\Scripts\python.exe scripts\local_healthcheck.py --skip-dotenv
```

Run a local licensed-data check:

```powershell
.\.venv\Scripts\python.exe scripts\local_healthcheck.py --data-mode local_dev --enable-drugbank --skip-dotenv
```

The healthcheck prints file/source status and runtime availability without printing secret values.

## Local LLM Strategy

This workstation currently has a small 4 GB NVIDIA GPU. It is not a good target for the large Ollama models planned for the cloud host.

Use one of these modes while cloud access is unavailable:

```dotenv
LLM_PROVIDER=mock
```

for deterministic backend tests, or:

```dotenv
LLM_PROVIDER=nvidia
```

for answer-quality testing through the API key already kept in the local `.env`.

Do not depend on cloud-host Ollama while cloud access is unavailable.

## Backend

Start FastAPI locally:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

## Frontend

Start the React frontend locally:

```powershell
npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

The frontend should call the local backend at `http://127.0.0.1:8000` during development.

## Tests

For tests that should not load `.env`:

```powershell
$env:INFERMED_SKIP_DOTENV='1'
.\.venv\Scripts\python.exe -m pytest tests\test_api_app.py tests\test_domain_architecture.py tests\test_tool_registry.py
```

Use local data tests only when the relevant parquet files are present.
