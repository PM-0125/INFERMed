# INFERMed Run Guidance

This guide describes the current local workstation runtime and the optional enhanced paths.

Cloud access is paused. Until it returns, development, data checks, backend tests, and frontend testing should run on this device.

## 1. Environment

Use the repository virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

If the environment is missing, recreate it from the repo root:

```powershell
python -m venv .venv --prompt infermed
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Runtime configuration is read from `.env`. Do not commit `.env` or API keys.

For current local development, use either deterministic mock output for tests or the local NVIDIA API configuration in `.env` for answer-quality work. This workstation has a 4 GB NVIDIA GPU, so it is not an appropriate target for the larger Ollama models planned for the cloud host.

Recommended local smoke-test mode:

```env
INFERMED_DATA_MODE=public_safe
ENABLE_DRUGBANK=false
ENABLE_QLEVER=false
LLM_PROVIDER=mock
```

Recommended local answer-quality mode:

```env
INFERMED_DATA_MODE=public_safe
ENABLE_DRUGBANK=false
ENABLE_QLEVER=false
LLM_PROVIDER=nvidia
NVIDIA_BASE_URL=https://integrate.api.nvidia.com
NVIDIA_MODEL=openai/gpt-oss-120b
LLM_STREAM=false
LLM_TIMEOUT_S=300
```

`NVIDIA_API_KEY` must be set locally in `.env`.

Cloud settings are deferred until the cloud host is available again:

```env
INFERMED_DATA_MODE=public_safe
ENABLE_DRUGBANK=false
ENABLE_QLEVER=false
LLM_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=gpt-oss:20b
OLLAMA_TIMEOUT_S=800
OLLAMA_NUM_PREDICT=3072
```

Recommended initial model pulls on the cloud host:

```bash
ollama pull gpt-oss:20b
ollama pull qwen3:30b
ollama pull bge-m3
```

Use `gpt-oss:20b` as the served default for multi-user testing. Keep `qwen3:30b` available for slower research/adjudication paths after the tool-calling architecture is in place.

Generated caches and datasets are local artifacts. They are ignored by Git and should be rebuilt or copied into `data/` on this workstation.

Local data paths:

```env
DUCKDB_DIR=data/duckdb
OPENFDA_CACHE_DIR=data/cache/openfda
CACHE_BACKEND=file
```

For a future deployment host, prefer:

```env
DUCKDB_DIR=/srv/infermed-data/duckdb
OPENFDA_CACHE_DIR=/srv/infermed-data/cache/openfda
CACHE_BACKEND=sqlite
SQLITE_CACHE_PATH=/srv/infermed-data/cache/infermed_cache.sqlite
```

The SQLite cache backend is used by the JSON/text cache helpers and is suitable for OpenFDA/source payload caching. Context caches can be regenerated and should not be treated as source data.

`NVIDIA_BASE_URL` can be the service root shown above, `https://integrate.api.nvidia.com/v1`, or the full `https://integrate.api.nvidia.com/v1/chat/completions` endpoint. The client normalizes these forms internally.

For GPT-OSS models, `NVIDIA_REASONING_EFFORT` can be `low`, `medium`, or `high`; NVIDIA defaults it to `medium`. Use `low` for interactive demos, `medium` for balanced output, and `high` only when slower, deeper analysis is acceptable:

```env
NVIDIA_REASONING_EFFORT=low
```

Before debugging a local NVIDIA setup, validate NVIDIA directly. Use `--stream` to match the frontend path and NVIDIA's example code:

```powershell
.\.venv\Scripts\python.exe scripts\check_nvidia.py --stream --timeout 120 --reasoning-effort low
```

## 2. Run Tests

First verify local data/runtime status:

```powershell
.\.venv\Scripts\python.exe scripts\local_healthcheck.py --skip-dotenv
.\.venv\Scripts\python.exe scripts\local_healthcheck.py --data-mode local_dev --enable-drugbank --skip-dotenv
```

Run the focused no-secret verification set:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_data_policy.py tests\test_evidence_schema.py tests\test_duckdb_query.py tests\test_llm.py tests\test_rag_pipeline.py tests\test_pkpd_utils.py
```

The full test suite may include live integration paths. Prefer focused tests before demo work unless you intentionally want live external checks.

## 3. Run The App

### Product Frontend Preview

The product-facing frontend is a React/Vite app under `frontend/`. It uses sample data unless `VITE_INFERMED_API_URL` points to the FastAPI backend.

Start the backend API from the repo root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

The frontend reads `frontend/.env.local`:

```env
VITE_INFERMED_API_URL=http://localhost:8000
```

Then start Vite:

```powershell
npm.cmd --prefix frontend install
npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Open `http://localhost:5173`.

Health check:

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

### Streamlit Research Console

Start Streamlit:

```powershell
.\.venv\Scripts\streamlit.exe run src\frontend\app.py
```

Use curated pairs first:

- simvastatin + clarithromycin
- warfarin + ibuprofen
- sildenafil + nitroglycerin
- clopidogrel + omeprazole
- digoxin + verapamil

The source-status panel should show QLever and DrugBank as disabled in public-safe mode.

Streamlit auto-reload is disabled in `.streamlit/config.toml` for demo stability. This avoids Streamlit crawling optional third-party ML modules and printing unrelated `torchvision` warnings. Restart the Streamlit command after code changes.

Context caches are stored under `data/cache/contexts/` with readable drug-pair names, for example `warfarin_fluconazole.json`. The cache lookup also checks the reversed pair and scans existing context JSON files by pair metadata, so reversed drug entry still reuses the same evidence cache. LLM response text is not cached; responses are regenerated from the current context.

## 4. Prepare Public DuckDB Data

Public-safe parquet conversion is handled by:

```powershell
.\.venv\Scripts\python.exe scripts\build_parquets.py twosides --csv data\raw\twosides.csv --out data\duckdb\twosides.parquet
.\.venv\Scripts\python.exe scripts\build_parquets.py dictrank --xlsx data\raw\dictrank_dataset_508.xlsx --out data\duckdb\dictrank.parquet
.\.venv\Scripts\python.exe scripts\build_parquets.py dilirank --xlsx data\raw\dilirank_diliscore_lit.xlsx --out data\duckdb\dilirank.parquet
.\.venv\Scripts\python.exe scripts\build_parquets.py diqt --xlsx "data\raw\diqt-drug information.xlsx" --out data\duckdb\diqt.parquet
```

Configured datasets are described in `data_manifest.yaml`. Missing public files are warnings/source-status entries, not startup failures.

Do not commit generated parquet files. Keep them under `data/duckdb/` locally or `/srv/infermed-data/duckdb` on the deployment host.

## 5. Optional OpenFDA

OpenFDA is public-safe and cached under `data/cache/openfda`.

If you have an OpenFDA API key, set this in `.env`:

```env
OPENFDA_API_KEY=...
```

FAERS/OpenFDA outputs are reporting signals only. Do not present them as causal incidence.

## 6. Optional QLever Enhancement

QLever is not required for the current hosted demo runtime and should remain disabled by default:

```env
ENABLE_QLEVER=false
```

Future/full-research use may enable it only after RDF indexes and endpoints are available:

```env
INFERMED_DATA_MODE=full_research_future
ENABLE_QLEVER=true
CORE_ENDPOINT=...
DISEASE_ENDPOINT=...
BIO_ENDPOINT=...
```

When QLever is disabled, the app should report it as disabled, not failed.

## 7. Optional Semantic Search And Reranking

Embedding search and cross-encoder reranking are optional local enhancements. They require model loading and may download model weights, so keep them disabled for the default external demo until the indexes are prepared:

```env
ENABLE_SEMANTIC_SEARCH=false
ENABLE_RERANKING=false
```

Enable them only when the local model cache/runtime is prepared:

```env
ENABLE_SEMANTIC_SEARCH=true
ENABLE_RERANKING=true
```

## 8. Optional DrugBank Local Data

DrugBank is restricted/local development only. Use it only if you have valid license permission.

Build licensed DrugBank parquet with:

```powershell
.\.venv\Scripts\python.exe scripts\build_drugbank_parquet.py xml --xml data\private\drugbank.xml --out data\duckdb\drugbank.parquet
```

Patch an older local DrugBank parquet with enzyme-action mapping:

```powershell
.\.venv\Scripts\python.exe scripts\build_drugbank_parquet.py patch-existing --parquet data\duckdb\drugbank.parquet
```

Then enable only for local development:

```env
INFERMED_DATA_MODE=local_dev
ENABLE_DRUGBANK=true
```

Do not move DrugBank data into public paths or commit it.

## 9. Evidence Mode (Developer / Contributor Reference)

The **evidence mode** is an internal configuration concept, not a user-facing feature. It is never displayed in the product UI.

### What it is

`INFERMED_DATA_MODE` (mapped to `Settings.data_mode`) controls which data sources are activated at runtime:

| `INFERMED_DATA_MODE` | DuckDB | QLever | DrugBank | Use case |
|---|---|---|---|---|
| `public_safe` | enabled | disabled | disabled | Hosted Ollama demo, public deployments |
| `local_dev` | enabled | disabled | enabled (if key set) | Local development with licensed data |
| `full_research_future` | enabled | enabled | optional | Full RDF + licensed dataset research |

`public_safe` enforces `ENABLE_DRUGBANK=false` and `ENABLE_QLEVER=false` regardless of other `.env` values (see `src/config/settings.py:104`). This is a data-governance rule, not a drug-specific shortcut.

### Where it surfaces in the API

`build_overview_card` in `src/api/transformers.py` emits an `"Evidence mode"` metric from `context["meta"]["data_mode"]`. This metric is intentionally filtered out by the React frontend (`App.tsx` overview tab) so it never reaches end users. Do not remove the backend field — it is used for internal logging and test assertions.

If you add a new metric that should also be developer-only, follow the same pattern: emit it from the transformer, then add its `label` to the `clinicalMetrics` filter in `App.tsx`.

### When to change data_mode

- **Hosted Ollama demo**: keep `public_safe`
- **Local testing with DrugBank parquet**: switch to `local_dev` and set `ENABLE_DRUGBANK=true`
- **Research path with full RDF**: switch to `full_research_future` and configure QLever endpoints

---

## 10. Enrichment Sources

INFERMed treats public enrichment as part of the normalized evidence contract, not as an optional mode. PubChem REST, UniProt, KEGG, Reactome, and ChEMBL are always enabled in `Settings` and their legacy `.env` disable flags are ignored. The REST enrichment pass runs independently from QLever, so public-safe mode can still resolve PubChem CIDs, PK descriptors, UniProt target details, KEGG pathway/enzyme hints, Reactome pathways, and ChEMBL activity data when QLever RDF is disabled.

| Source | `Settings` flag | Runtime policy | What it adds |
|---|---|---|---|
| **UniProt** | `enable_uniprot` | Always on | Protein function, transporter class, gene name for each target |
| **KEGG** | `enable_kegg` | Always on | Drug metabolism maps, enzyme participation, pathway IDs |
| **Reactome** | `enable_reactome` | Always on | Mechanistic biological pathways for drug targets |
| **ChEMBL** | `enable_chembl` | Always on | Bioactivity data (IC50, Ki) for target-compound pairs |

### How enrichment data flows to the frontend

1. `src/retrieval/qlever_query.py` (and related clients) populate `context["signals"]["mechanistic"]`
2. `build_mechanisms_card` in `src/api/transformers.py` maps these into `evidence.mechanisms.rows`
3. The React Mechanisms tab renders enzyme rows with role pills (substrate/inhibitor/inducer) and target/pathway rows as tag chips

Enrichment is best-effort per request: if a source is unreachable or returns no data, the normalized context still contains an empty structured slot and records a caveat where applicable rather than causing a failure. Source availability is reported in `evidence.sources` (the Sources tab), while returned rows live in the Mechanisms tab.

### Adding new enrichment fields

Current supported enrichment fields include:

- `uniprot_ids_a` / `uniprot_ids_b`
- `uniprot_targets_a` / `uniprot_targets_b`
- `kegg_pathways_a` / `kegg_pathways_b` / `kegg_common_pathways`
- `kegg_enzymes_a` / `kegg_enzymes_b`
- `reactome_pathways_a` / `reactome_pathways_b`
- `chembl_enrichment`

`build_mechanisms_card` maps these into `evidence.mechanisms.rows`, and `llm_interface.py` includes them in `{{EVIDENCE_TABLE}}` and `{{SOURCES}}` when present. The React frontend renders standard row data automatically. New row types still require a new `meta` value and, if needed, a matching sub-section in the Mechanisms tab renderer in `App.tsx`.

---

## 11. Deferred Material

Legacy/ad hoc helpers and old RDF notes are kept locally under `scripts/deferred/`, which is intentionally ignored by Git. Treat that folder as scratch/reference material, not part of the active demo workflow.

PubChem RDF file lists used to construct historical CORE/DISEASE/BIO QLever endpoints are also deferred under `scripts/deferred/pubchem_list/`. They are not needed for the public-safe app runtime.
