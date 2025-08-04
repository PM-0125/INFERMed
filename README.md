# INFERMed: Intelligent Navigator for Evidence-based Retrieval in Medicine

> ⚠️ **Notice**: This system is a work in progress as part of an academic master's thesis. While it uses verified datasets and knowledge sources, it is not intended for direct clinical use without supervision from licensed professionals.

## Overview

**INFERMed** is a biomedical drug interaction checker built using a **Retrieval-Augmented Generation (RAG)** architecture. It intelligently predicts and explains potential drug–drug interactions by combining pharmacokinetic/pharmacodynamic (PK/PD) knowledge, real-world adverse event reports, and curated clinical datasets. These insights are synthesized using a locally hosted large language model (LLM), offering personalized and context-aware recommendations for different user types.

---

## 🧠 Data Sources and Knowledge Integration

* **PubChem RDF (Knowledge Graph)**
  Queried via **QLever SPARQL engine** to extract graph-based knowledge such as drug targets, metabolic pathways, and protein interactions.

* **Tabular Clinical Datasets (DuckDB)**
  Stored in **Parquet format** and accessed with DuckDB for high-speed interaction lookups:

  * `TwoSides`: Side-effect pairs and PRR (Proportional Reporting Ratio)
  * `DILIrank`, `DICTRank`, `DIQT`: Risk rankings for liver injury, cardiotoxicity, and QT prolongation
  * `DrugBankXML`: Drug mechanisms, targets, and known interactions (converted to Parquet)

* **OpenFDA API**
  Used to query real-world adverse event data from FAERS (FDA Adverse Event Reporting System). Responses are **cached locally** in structured format to improve speed and reduce API dependency.

* **Local LLM (Mistral via Ollama)**
  A compact, fast, locally hosted language model that generates final natural language responses using retrieved evidence as context.

---

## 👥 User Modes

The Streamlit-based UI provides three tailored interaction modes:

* 🧺 **Doctor Mode**: Detailed mechanistic explanations with biochemical and molecular insights
* 🧕‍♂️ **Patient Mode**: Simplified advice and warnings in layman-friendly language
* 🧪 **Pharma Mode**: In-depth safety and statistical context, ideal for research or regulatory use

---

## 🔧 System Architecture

Modular backend components are organized as follows:

* `src/retrieval/duckdb_query.py`
  Retrieves structured interaction data from Parquet datasets using DuckDB.

* `src/retrieval/qlever_query.py`
  Interfaces with QLever to extract graph-based PK/PD relationships from PubChem RDF.

* `src/retrieval/openfda_api.py`
  Queries and caches FDA-reported adverse event data via the OpenFDA API.

* `src/llm/llm_interface.py`
  Interfaces with a local LLM (e.g., Mistral) using structured prompts and pre-assembled context.

* `src/llm/rag_pipeline.py`
  Orchestrates retrieval from DuckDB, QLever, and OpenFDA. Selects prompt templates based on user mode and generates the final explanation via LLM.

* `src/frontend/app.py`
  The Streamlit-based frontend for entering drug names, selecting user mode, and viewing interaction explanations.

* `src/utils/`
  Shared utility functions (caching, parsing, pathway analysis, etc.).

---

## 📁 Repository Structure

```bash
INFERMed/
├── data/
│   ├── duckdb/      # Parquet and/or DuckDB files (e.g., twosides.parquet)
│   ├── openfda/     # Cached OpenFDA JSON/Parquet responses
│   └── pubchem/     # Filtered PubChem RDF .ttl or QLever index
├── models/          # Local LLM models (not tracked in Git)
├── scripts/         # Setup and utility scripts (e.g., scaffold.ps1)
├── src/
│   ├── frontend/    # Streamlit UI
│   ├── llm/         # RAG orchestration and LLM interface
│   ├── retrieval/   # Query interfaces for DuckDB, OpenFDA, QLever
│   └── utils/       # Shared utilities and PK/PD tools
├── tests/           # Unit tests and sample validation inputs
├── requirements.txt # Python dependencies
├── .gitignore       # Excludes datasets, cache, models
└── README.md        # This document
```

---

## 🚀 Getting Started

1. **Set up environment**

   ```bash
   pip install -r requirements.txt
   ```

   *(Ensure Python 3.10+ is installed. Ollama should also be set up for local LLM hosting.)*

2. **Prepare datasets**
   Download and place the following files in `data/duckdb/`:

   * `twosides.parquet`
   * `DILIrank.parquet`
   * `DIQT.parquet`
   * `DICTRank.parquet`
   * `DrugBankXML.parquet`

3. **Configure PubChem knowledge graph**

   * Filter relevant `.ttl` files and place them in `data/pubchem/`
   * Build a QLever index if needed and connect via `qlever_query.py`

4. **Run the app**

   ```bash
   streamlit run src/frontend/app.py
   ```

---

## ⚡ Performance Tips

* Cached OpenFDA results in `data/openfda/` prevent unnecessary API calls
* Use filtered PubChem data to avoid massive memory overhead
* Consider converting all Parquet files into a `.duckdb` database for compact storage and faster joins
* Tune prompt templates (`prompt_templates.txt`) per user mode to optimize LLM responses

---

## 🧚️ Testing & Evaluation

* Run functional tests in `tests/test_interactions.py`
* Evaluate system on common DDI pairs (e.g., simvastatin + clarithromycin)
* Compare INFERMed explanations to baseline tools like Drugs.com or Medscape

---

## 💪 Future Extensions

* Add drug–gene or protein–protein interaction graphs
* Incorporate vector search for literature context (e.g., PubMed abstracts)
* Add multilingual support (e.g., Polish mode for local deployment)
* Expand LLM reasoning with Chain-of-Thought prompting or QA-GNN integration

---

## 🤝 Contribution & License

This codebase is part of a personal academic research project and is not intended for public or commercial deployment without explicit permission.

> **Disclaimer:** INFERMed combines deterministic querying and probabilistic language generation to provide informative summaries about drug–drug interactions. While all data sources used are scientifically verified or publicly available, **this tool is not a substitute for medical advice**. All final decisions must be made by **licensed healthcare professionals** or **qualified experts in pharmaceutical safety**. Patients should always consult their doctor or pharmacist before acting on any output from this system.

---

**INFERMed** – because understanding what happens *between the lines* (of prescriptions) can save lives.
