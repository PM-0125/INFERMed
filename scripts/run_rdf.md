# === env (same as before) ===
export PUBCHEM_ROOT="/home/pranjul/mydata/Medical Assistant/Fuseki/PubChemRDF-data"
export LISTS_DIR="/home/pranjul/mydata/INFERMed/data/pubchem_list"
export QLIDX="/mnt/data_vault/qlever-indexes"
export QLEVER_BIN="/mnt/data_vault/qlever/build"
export LOGDIR="/mnt/data_vault/qlever/logs"
mkdir -p "$LOGDIR"

# (optional) helper: NT converter used in your build scripts
convert_turtle_to_nt(){ rapper -q -i turtle -o ntriples -I "http://example/" - 2>/dev/null; }          

cd "$QLEVER_BIN"

CORE_IDX="$QLIDX/core/core"
DISEASE_IDX="$QLIDX/disease/disease"
BIO_IDX="$QLIDX/bioactivity/bioactivity"   # for later, once done

# Start servers (adjust ports if you like)
./ServerMain -i "$CORE_IDX" -p 7010 &
./ServerMain -i "$DISEASE_IDX" -p 7011 &
./ServerMain -i "$BIO_IDX" -p 7012 &

# later:
# ./ServerMain -i "$BIO_IDX" -p 7012 &     

export CORE_ENDPOINT="http://localhost:7010/"
export DISEASE_ENDPOINT="http://localhost:7011/"
export BIO_ENDPOINT="http://localhost:7012/"   # once bioactivity is up
 
# Kill Server:
pkill -f 'ServerMain -i .*core/core'      # stop core
pkill -f 'ServerMain -i .*disease/disease' # stop disease
pkill -f 'ServerMain -i .*bioactivity/bioactivity'  #stop bioactivity

# To check if server is occupied:
ss -ltnp | grep -E ':7010|:7011'
# or
lsof -nP -iTCP:7010 -sTCP:LISTEN
lsof -nP -iTCP:7011 -sTCP:LISTEN

# Before running the tests:
set -a
source .env
set +a

# RUnning the frontend
export PYTHONPATH=.
streamlit run src/frontend/app.py

# UNIT Testing
export PYTHONPATH=.
pytest -q
# or targeted:
pytest -q tests/test_llm.py
pytest -q tests/test_pkpd_utils.py
pytest -q tests/test_rag_pipeline.py


# Project Structure:

INFERMed/
├── .gitignore
├── README.md
├── requirements.txt
├── data/
│   ├── duckdb/
│   ├── openfda/
│   └── pubchem/
├── models/
├── scripts/
│   └── scaffold.ps1
├── src/
│   ├── frontend/
│   │   └── app.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── llm_interface.py
│   │   ├── prompt_templates.txt
│   │   └── rag_pipeline.py
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── duckdb_query.py
│   │   ├── openfda_api.py
│   │   └── qlever_query.py
│   └── utils/
│       ├── __init__.py
│       ├── caching.py
│       └── pkpd_utils.py
└── tests/
    └── test_interactions.py
    └── test_duckdb_query.py
    └── test_llm.py
    └── test_openfda_api.py
    └── test_qlever_query.py



🧩 MODULE-WISE DIVISION OF WORK (Chat-Worthy Units)

Each of the following work units can be treated as an independent task—you can open a separate chat and say “Let’s work on Workstream X”, and we’ll focus only on that module.

🔁 [Workstream 1] – DuckDB Parquet Retrieval Module

Files:
src/retrieval/duckdb_query.py

Tasks:
Load & query twosides.parquet, DILIrank.parquet, DICTRank.parquet, DIQT.parquet, DrugBankXML.parquet
Create reusable query functions like:
get_side_effects(drug_name)
get_interaction_score(drug1, drug2)
get_dili_risk(drug_name)

🌐 [Workstream 2] – QLever SPARQL Module
Files:
src/retrieval/qlever_query.py

Tasks:
Build SPARQL query wrappers to hit QLever endpoint
Implement queries like:
get_targets(drug_smiles)
get_common_pathways(drug1, drug2)
get_metabolism_profile(drug_id)

🌍 [Workstream 3] – OpenFDA API + Caching

Files:
src/retrieval/openfda_api.py

Tasks:
Query adverse events by drug name
Cache results in data/openfda/ as JSON

Implement:
get_faers_data(drug_name)
get_common_reactions(drug1, drug2)

🤖 [Workstream 4] – LLM Interface

Files:
src/llm/llm_interface.py
src/llm/prompt_templates.txt

Tasks:
Connect to local Ollama/Mistral endpoint
Implement generate_response(prompt, mode)

Create prompt templates for:
Doctor
Patient
Pharma

🧠 [Workstream 5] – RAG Orchestrator

Files:
src/llm/rag_pipeline.py

Tasks:
Integrate DuckDB, QLever, OpenFDA modules
Join their outputs into context string
Call llm_interface.generate_response(...)
Output formatted answer with traceability

🖥️ [Workstream 6] – Streamlit Frontend

Files:
src/frontend/app.py

Tasks:
Input: 2 drug names, select user mode
Display:
Summary
Side effects
Risk warnings
Optional: add charts / visual side effect frequency

🧪 [Workstream 7] – Testing & Sample Cases

Files:
tests/test_interactions.py

Tasks:
Add known DDI test cases
Validate results vs. online tools
Write unit tests for each module

📊 Suggested Execution Order:
Phase	Workstreams to Start
✅ Now	[1], [3], [4] (data + OpenFDA + LLM)
🧠 Mid	[2], [5] (QLever + RAG)
🖼️ UI	[6]
✔ Final	[7]