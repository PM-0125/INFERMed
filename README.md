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

* **External REST APIs (Enrichment)**
  Additional APIs are integrated to enrich and disambiguate data:
  * **UniProt REST API**: Protein-level data (targets, transporters, enzymes) with functional annotations
  * **KEGG REST API**: Drug pathways, metabolism maps, and enzyme interactions
  * **Reactome REST API**: Mechanistic biological pathways involving drug targets
  * **PubChem REST API**: Protein label resolution and pharmacokinetic properties (molecular weight, LogP, H-bonding)

* **Canonical PK/PD Dictionary**
  A curated local JSON dictionary (`data/dictionary/canonical_pkpd.json`) providing authoritative, well-established interaction data with detailed mechanism descriptions, severity ratings, and evidence levels.

* **Local LLM (via Ollama)**
  A locally hosted language model that generates final natural language responses using retrieved evidence as context. Supports multiple models including Mistral, MedGemma, and others.

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
  Interfaces with QLever to extract graph-based PK/PD relationships from PubChem RDF. Also integrates UniProt, KEGG, and Reactome APIs for target enrichment and pathway discovery.

* `src/retrieval/openfda_api.py`
  Queries and caches FDA-reported adverse event data via the OpenFDA API.

* `src/retrieval/uniprot_client.py`
  Client for UniProt REST API to obtain protein metadata, functional annotations, and transporter classifications.

* `src/retrieval/kegg_client.py`
  Client for KEGG REST API to retrieve drug pathways, metabolism maps, and common pathway analysis.

* `src/retrieval/reactome_client.py`
  Client for Reactome REST API to discover mechanistic biological pathways involving drug targets.

* `src/retrieval/pubchem_client.py`
  Client for PubChem REST API to resolve protein labels and retrieve pharmacokinetic properties.

* `src/utils/pkpd_utils.py`
  Synthesizes PK/PD evidence from multiple sources, detects enzyme/target/pathway overlaps, and integrates canonical interaction data. Generates compact risk summaries for LLM consumption.

* `src/llm/llm_interface.py`
  Interfaces with a local LLM via Ollama using structured prompts and pre-assembled context. Handles prompt template selection, context summarization, and response generation.

* `src/llm/rag_pipeline.py`
  Orchestrates sequential retrieval from DuckDB, QLever, and OpenFDA. Integrates external API enrichment and canonical dictionary lookups. Selects prompt templates based on user mode and generates the final explanation via LLM.

* `src/frontend/app.py`
  The Streamlit-based frontend for entering drug names, selecting user mode, and viewing interaction explanations with supporting evidence.

* `src/utils/`
  Shared utility functions (caching, parsing, pathway analysis, normalization, etc.).

---

## 📁 Repository Structure

```bash
INFERMed/
├── data/
│   ├── duckdb/         # Parquet files (e.g., twosides.parquet)
│   ├── dictionary/     # Canonical PK/PD dictionary (canonical_pkpd.json)
│   ├── openfda/        # Cached OpenFDA JSON responses
│   ├── cache/          # Cached contexts and LLM responses
│   └── pubchem/        # Filtered PubChem RDF .ttl or QLever index
├── models/             # Local LLM models (not tracked in Git)
├── scripts/            # Setup and utility scripts
├── src/
│   ├── frontend/       # Streamlit UI
│   ├── llm/            # RAG orchestration and LLM interface
│   ├── retrieval/      # Query interfaces (DuckDB, QLever, OpenFDA, UniProt, KEGG, Reactome, PubChem)
│   └── utils/          # Shared utilities and PK/PD synthesis tools
├── tests/              # Unit tests and sample validation inputs
├── requirements.txt    # Python dependencies
├── .gitignore         # Excludes datasets, cache, models
└── README.md          # This document
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
   * Set environment variables for QLever endpoints:
     ```bash
     export CORE_ENDPOINT=<your_qlever_core_endpoint>
     export DISEASE_ENDPOINT=<your_qlever_disease_endpoint>
     export BIO_ENDPOINT=<your_qlever_bio_endpoint>  # Optional but recommended
     ```

4. **Set up canonical PK/PD dictionary** (optional but recommended)
   * Place `canonical_pkpd.json` in `data/dictionary/` for authoritative interaction data

5. **Run the app**

   ```bash
   streamlit run src/frontend/app.py
   ```

---

## ⚡ Performance Tips

* **Caching**: The system implements multi-level caching:
  * OpenFDA API responses are cached in `data/openfda/`
  * Assembled contexts are cached in `data/cache/contexts/`
  * Generated LLM responses are cached in `data/cache/responses/`
* **Context Truncation**: To manage context size, the system applies top-K truncation:
  * Side effects: top 25 per drug
  * FAERS reactions: top 10 per drug and for combinations
  * Targets: top 32 per drug
  * Pathways: top 24 per drug
* **Timeout Management**: 
  * QLever SPARQL queries: 90 seconds
  * OpenFDA API: 8 seconds with retry logic
  * Enrichment APIs (UniProt, KEGG, Reactome, PubChem): 10-15 seconds
* Use filtered PubChem data to avoid massive memory overhead
* Tune prompt templates (`src/llm/prompt_templates.txt`) per user mode to optimize LLM responses

---

## 🧚️ Testing & Evaluation

* Run functional tests in `tests/`
* Evaluate system on common DDI pairs (e.g., simvastatin + clarithromycin, warfarin + ciprofloxacin)
* Test with multiple drug combinations to verify API integrations and canonical dictionary usage
* Compare INFERMed explanations to baseline tools like Drugs.com or Medscape
* Check evidence grounding: all claims should be traceable to retrieved data sources

---

## 💪 Recent Enhancements

* **External API Integration**: Added UniProt, KEGG, and Reactome REST APIs for comprehensive target and pathway enrichment
* **Canonical PK/PD Dictionary**: Integrated authoritative interaction data with detailed mechanisms and severity ratings
* **Enhanced PK/PD Synthesis**: Improved overlap detection and risk summarization with multi-source evidence integration
* **Evidence Grounding**: Strict evidence-first reasoning with explicit source attribution and caveat documentation

## 🔮 Future Extensions

* Add drug–gene or protein–protein interaction graphs
* Incorporate vector search for literature context (e.g., PubMed abstracts)
* Add multilingual support (e.g., Polish mode for local deployment)
* Expand LLM reasoning with Chain-of-Thought prompting or QA-GNN integration
* Implement parallel retrieval for improved latency

---

## 🤝 Contribution & License

This codebase is part of a personal academic research project and is not intended for public or commercial deployment without explicit permission.

> **Disclaimer:** INFERMed combines deterministic querying and probabilistic language generation to provide informative summaries about drug–drug interactions. While all data sources used are scientifically verified or publicly available, **this tool is not a substitute for medical advice**. All final decisions must be made by **licensed healthcare professionals** or **qualified experts in pharmaceutical safety**. Patients should always consult their doctor or pharmacist before acting on any output from this system.

---

**INFERMed** – because understanding what happens *between the lines* (of prescriptions) can save lives.
