# INFERMed

**Intelligent Navigator for Evidence-based Retrieval in Medicine**

INFERMed is a research-stage, evidence-first drug interaction platform. It combines deterministic retrieval, curated PK/PD knowledge, public biomedical sources, local analytical datasets, and LLM summarization to produce inspectable drug-drug interaction assessments.

The project began as published thesis research and is now being refined into a product-grade research platform with medical and pharmacovigilance reviewers.

Published chapter: [INFERMed: A PK/PD-Aware Retrieval-Augmented System for Explainable Drug-Drug Interaction Analysis](https://link.springer.com/chapter/10.1007/978-3-032-23241-0_9)

## License And Use Boundary

INFERMed is **source-available for non-commercial research, evaluation, and educational use** under the [PolyForm Noncommercial License 1.0.0](LICENSE).

This is intentionally not an OSI open-source license because commercial use is not permitted.

Permitted examples:

- Academic research and reproducibility review
- Non-commercial evaluation by clinicians, pharmacovigilance reviewers, and biomedical researchers
- Educational demonstrations
- Non-commercial contributions back to this repository

Not permitted without prior written permission:

- Commercial products or services
- Paid hosted access or SaaS use
- Clinical deployment or operational medical decision support
- Resale, sublicensing, or white-labeling
- Use in proprietary commercial workflows
- Commercial derivatives or competing commercial platforms

Unauthorized use outside the license terminates the granted rights and may expose the user or organization to legal remedies.

## Medical Safety Notice

INFERMed is research software. It is **not a medical device**, **not certified clinical decision support**, and **not a substitute for licensed clinical judgment, approved labeling, institutional policy, or patient-specific care**.

Outputs may contain model errors, incomplete evidence, source limitations, or general pharmacology assumptions. All outputs must be reviewed by qualified medical, pharmacy, or pharmacovigilance professionals before any real-world interpretation.

Patients should never act on INFERMed output without consulting a licensed clinician or pharmacist.

## What INFERMed Does

For a drug pair, INFERMed assembles an interaction record from multiple evidence layers, normalizes the evidence into structured JSON, and asks an LLM to explain what the retrieved evidence supports.

The response is designed to remain inspectable:

- AI explanation is shown beside evidence cards.
- Evidence is divided by source and mechanism.
- PK/PD reasoning remains visible instead of hidden inside the model answer.
- Source limitations are preserved.
- Public-safe and licensed/local modes are separated.

Current primary workflow:

```text
User enters drug pair
  -> FastAPI backend retrieves evidence
  -> Evidence is normalized into an interaction record
  -> PK/PD utilities synthesize mechanism and risk signals
  -> LLM writes a role-aware explanation
  -> React frontend displays answer plus evidence cards
```

## Current Architecture

```text
frontend/                  React + Vite user interface
src/api/                   FastAPI application
src/llm/                   RAG pipeline, prompt templates, LLM provider interface
src/retrieval/             Source clients and retrieval adapters
src/utils/                 PK/PD synthesis, cache helpers, normalization utilities
data/                      Project dictionaries and local-only generated data folders
scripts/                   Data preparation and utility scripts
run_guidance/              How-to-run notes
tests/                     Unit and integration-oriented tests
```

## Evidence Sources

INFERMed uses a tiered evidence model rather than treating all sources as equal.

### Public and Public-Safe Sources

- **OpenFDA FAERS**: adverse-event reporting signals. These are associative reports, not causal proof.
- **PubChem REST / PubChemRDF**: compound identifiers, structures, and RDF-backed biomedical relationships.
- **ChEMBL**: bioactivity and target-related enrichment when available.
- **UniProt**: protein target, enzyme, and transporter metadata.
- **KEGG**: pathway and drug metabolism context.
- **Reactome**: biological pathway enrichment.
- **Canonical PK/PD dictionary**: curated local mechanism hints and known interaction summaries.

### Local Analytical Sources

- **TWOSIDES**: side-effect pair and PRR-derived signal context.
- **DILIrank**: drug-induced liver injury concern.
- **DICT / DICTRank**: cardiotoxicity-oriented risk context.
- **DIQT**: QT-prolongation-oriented risk context.

### Restricted Or Licensed Sources

DrugBank-derived files are not licensed for general redistribution through this repository. If used, they must be supplied separately by a user or institution with valid permission.

See [NOTICE.md](NOTICE.md) and [data_license.md](data_license.md) for third-party data, source links, and API boundaries.

Generated datasets, source caches, embedding indexes, and licensed/private data are not committed to Git. Build or copy those artifacts into `data/` locally, or into an external deployment volume such as `/srv/infermed-data` on the host.

## Data Modes

Recommended public demo mode:

```dotenv
INFERMED_DATA_MODE=public_safe
ENABLE_DRUGBANK=false
```

Private licensed/local evaluation only:

```dotenv
INFERMED_DATA_MODE=local_dev
ENABLE_DRUGBANK=true
```

Do not enable restricted datasets for public demos unless licensing and deployment permissions are clear.

## Data And Cache Storage

The repository keeps only small project-authored dictionaries under version control. Bulk parquet files, source caches, API payload caches, and licensed datasets are local/deployment artifacts.

Default local cache behavior remains file based:

```dotenv
CACHE_BACKEND=file
OPENFDA_CACHE_DIR=data/cache/openfda
```

For a shared cloud host, use the SQLite cache backend so generated API/source payloads live in one durable database:

```dotenv
CACHE_BACKEND=sqlite
SQLITE_CACHE_PATH=/srv/infermed-data/cache/infermed_cache.sqlite
OPENFDA_CACHE_DIR=/srv/infermed-data/cache/openfda
DUCKDB_DIR=/srv/infermed-data/duckdb
```

SQLite is a deployment convenience for source payload caching. It does not replace the analytical parquet datasets.

## Local Development

Create and use the repository virtual environment instead of global Python.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the backend:

```powershell
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Run the frontend:

```powershell
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Build the frontend:

```powershell
cd frontend
npm run build
```

## Environment

The backend reads runtime configuration from `.env`. Do not commit `.env`, API keys, provider keys, licensed dataset paths, or private deployment notes.

Recommended hosted cloud settings use local Ollama models on the GPU host:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=gpt-oss:20b
OLLAMA_TIMEOUT_S=800
OLLAMA_NUM_PREDICT=3072
LLM_TEMPERATURE=0.1
LLM_TOP_P=0.9
LLM_MAX_TOKENS=3072
LLM_STREAM=false
```

Recommended initial model set for the 48 GB VRAM host:

- `gpt-oss:20b`: primary interactive explanation and tool-calling model.
- `qwen3:30b`: deeper research/adjudication model for slower, higher-effort runs.
- `bge-m3`: embedding model for retrieval and future semantic cache/index work.

NVIDIA API settings are useful for local development or fallback testing on a different machine, but should not be required on the cloud host:

```dotenv
LLM_PROVIDER=nvidia
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=openai/gpt-oss-120b
NVIDIA_REASONING_EFFORT=low
NVIDIA_STREAM=true
LLM_TEMPERATURE=0.1
LLM_TOP_P=0.9
LLM_MAX_TOKENS=3072
LLM_STREAM=true
LLM_TIMEOUT_S=800
NVIDIA_RETRY_ATTEMPTS=2
```

Use NVIDIA `medium` reasoning only after latency and gateway stability are acceptable for the demo.

## Testing

Run backend tests:

```powershell
pytest
```

Run focused checks:

```powershell
pytest tests/test_llm.py
pytest tests/test_pkpd_utils.py tests/test_rag_pipeline.py
```

Run frontend checks:

```powershell
cd frontend
npm run lint
npm run build
```

## Deployment Direction

For the current external testing phase, the recommended deployment is a single cloud host:

```text
Caddy
  -> serves frontend/dist
  -> proxies /api to FastAPI on 127.0.0.1:8000
```

The project can be source-controlled on GitHub, but GitHub Pages cannot host the backend. Vercel can host frontend previews later, but the simplest reliable demo is frontend and backend on the same cloud machine behind Caddy.

## Contributing

Contributions are welcome for non-commercial research use. By contributing, you agree that your contribution is provided under the same non-commercial project license and does not introduce restricted data, secrets, or commercial-use rights.

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before opening issues or pull requests.

## Contact

For research collaboration, licensing, or commercial permission requests:

- Pranjul Mishra
- Email: pranjul.mishra@proton.me
- GitHub: [PM-0125](https://github.com/PM-0125)
