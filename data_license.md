# INFERMed Data Sources And License Notes

This file tracks the original source links, intended use, and redistribution cautions for data and API sources used by INFERMed.

The INFERMed code license does not relicense third-party data. Each upstream source keeps its own license, terms of use, attribution requirements, and access limits.

## Current Data Policy

INFERMed separates public-safe evidence from restricted/local evidence.

The Git repository does not distribute generated caches, parquet datasets, bulk RDF data, or licensed/private files. Source links below are provided so researchers can obtain data from the original publisher and build local artifacts under the relevant upstream terms.

Public-safe mode:

```dotenv
INFERMED_DATA_MODE=public_safe
ENABLE_DRUGBANK=false
```

Private licensed/local mode:

```dotenv
INFERMED_DATA_MODE=local_dev
ENABLE_DRUGBANK=true
```

Do not publish or host restricted datasets unless the project has explicit redistribution and deployment permission.

## Source Inventory

| Source | INFERMed use | Official source link | Local artifact or client | Redistribution note |
|---|---|---|---|---|
| OpenFDA drug adverse event API / FAERS | Post-market adverse-event signal retrieval and reaction counts | https://open.fda.gov/apis/drug/event/ | `src/retrieval/openfda_api.py`, `data/cache/openfda/` | Public API. FAERS reports are associative, not causal, and cannot estimate incidence by themselves. |
| PubChem PUG-REST | Compound identifiers, names, structures, and properties | https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest | `src/retrieval/pubchem_client.py` | Follow NCBI/PubChem usage policies and attribution expectations. |
| PubChem RDF | RDF knowledge graph source for optional QLever-backed retrieval | https://pubchem.ncbi.nlm.nih.gov/docs/rdf | Optional QLever indexes / RDF preparation | Do not redistribute bulk RDF derivatives without checking PubChem/NCBI terms and generated-file size policy. |
| ChEMBL | Bioactivity and target enrichment | https://www.ebi.ac.uk/chembl/ | `src/retrieval/chembl_client.py` | ChEMBL states its data is provided under CC BY-SA 3.0 on its official page. Preserve attribution and source label. |
| UniProt | Protein, enzyme, target, and transporter metadata | https://www.uniprot.org/ and https://www.uniprot.org/help/api | `src/retrieval/uniprot_client.py` | Follow UniProt license and citation requirements for any redistributed derived data. |
| KEGG REST API | Drug, pathway, enzyme, and metabolism context | https://www.kegg.jp/kegg/rest/keggapi.html | `src/retrieval/kegg_client.py` | KEGG has specific academic/commercial subscription and redistribution terms. Treat derived KEGG outputs cautiously. |
| Reactome Content Service | Biological pathway enrichment | https://reactome.org/dev/content-service | `src/retrieval/reactome_client.py` | Follow Reactome citation and license requirements for derived pathway data. |
| TWOSIDES | Pairwise side-effect and PRR signal context | https://tatonettilab.org/resources/tatonetti-stm.html | `data/duckdb/twosides.parquet` | Research dataset from Tatonetti Lab resources. Preserve citation and do not present PRR as causality. |
| DILIrank | Drug-induced liver injury concern scores/classes | https://www.fda.gov/science-research/liver-toxicity-knowledge-base-ltkb/drug-induced-liver-injury-rank-dilirank-20-dataset | `data/duckdb/dilirank.parquet` | FDA LTKB dataset. Preserve FDA source and DILIrank citation. |
| DICTRank | Drug-induced cardiotoxicity score/context | Provenance link pending verification | `data/duckdb/dictrank.parquet` | Keep as research/local analytical artifact until original source URL and redistribution terms are verified. |
| DIQT | Drug-induced QT-prolongation score/context | Provenance link pending verification | `data/duckdb/diqt.parquet` | Keep as research/local analytical artifact until original source URL and redistribution terms are verified. |
| Canonical PK/PD dictionary | Project-curated known PK/PD interaction summaries | Project-authored local dictionary | `data/dictionary/canonical_pkpd.json` | Project-authored unless entries are copied from external sources. Do not paste proprietary label/database text without permission. |
| DrugBank | Optional licensed local drug mechanisms, targets, and interactions | https://go.drugbank.com/ | Restricted local parquet only | Restricted. Must not be committed, redistributed, or enabled publicly without valid DrugBank permission. |

## Source-Specific Caveats

### OpenFDA / FAERS

FAERS records are spontaneous adverse-event reports. openFDA notes that these reports are not proof of causality and cannot establish incidence. INFERMed must describe FAERS-derived values as signal context only.

### TWOSIDES PRR

PRR means proportional reporting ratio. In INFERMed it is used as an adverse-event reporting signal, not as a causal estimate or clinical incidence rate.

### DILIrank

DILIrank represents drug-induced liver injury concern categories/scores derived from FDA labeling and literature review. It should be presented as liver-injury concern context, not as patient-specific prediction.

### DICTRank And DIQT

These are currently used as local analytical toxicity/QT context tables. Their upstream source URLs and redistribution terms should be verified before a public release that depends on them.

Until verification is complete:

- Keep source labels visible.
- Avoid claiming they are official clinical standards.
- Do not use them as the sole basis for a clinical recommendation.
- Reconfirm whether the generated parquet files are allowed to be redistributed.

### DrugBank

DrugBank is restricted/licensed. INFERMed's repository license does not grant DrugBank rights. A user or institution must supply DrugBank data separately and must comply with DrugBank terms.

## Required Citation Hygiene

When publishing results, demos, screenshots, or papers using INFERMed, cite the relevant upstream data sources used for that result. At minimum, cite:

- INFERMed published chapter and repository.
- OpenFDA/FAERS if FAERS evidence is used.
- PubChem if compound identifiers, structures, or RDF data are used.
- ChEMBL, UniProt, KEGG, Reactome if enrichment data appears in the output.
- TWOSIDES, DILIrank, DICTRank, DIQT if local analytical risk/context scores are used.
- DrugBank only when a licensed local DrugBank source was enabled.

## Maintainer Checklist Before Public Deployment

- Confirm `.env` is not committed.
- Confirm `ENABLE_DRUGBANK=false`.
- Confirm restricted datasets are not present in public paths.
- Confirm generated caches do not include proprietary or sensitive source payloads.
- Confirm `data_manifest.yaml` matches the deployed data mode.
- Confirm any source with uncertain provenance is disabled or clearly marked.
