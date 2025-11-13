# Enzyme Action Mapping and PubChem REST API Integration

## Summary

This document describes the improvements made to:
1. **Enzyme-Action Mapping**: Proper per-enzyme action storage in DrugBank parquet
2. **PubChem REST API Integration**: Human-readable protein labels and PK data enrichment

## 1. Enzyme-Action Mapping Fix

### Problem
Previously, enzyme actions were stored as a flat list, making it difficult to map specific actions to specific enzymes. The code tried to map by index, but this failed when there were fewer actions than enzymes.

### Solution
- **Structured Storage**: Added `enzyme_action_map` column to DrugBank parquet as JSON string
- **Format**: `[{"enzyme": "CYP3A4", "actions": ["substrate", "inhibitor"]}, ...]`
- **Backward Compatibility**: Still stores flat `enzymes` and `enzyme_actions` lists

### Changes Made

#### `scripts/build_parquets.py`
- Extracts enzyme data per-enzyme with actions
- Stores as structured JSON in `enzyme_action_map` column
- Maintains backward compatibility with flat lists

#### `src/retrieval/duckdb_query.py`
- Updated `get_drug_enzymes()` to return `enzyme_action_map`
- Parses JSON string to return structured mapping
- Falls back to flat list if `enzyme_action_map` is not available

#### `src/retrieval/qlever_query.py`
- Uses `enzyme_action_map` when available (preferred)
- Falls back to flat list for backward compatibility
- Properly categorizes enzymes by all their actions

## 2. PubChem REST API Integration

### Purpose
- **Human-Readable Protein Labels**: Convert cryptic protein IDs (e.g., "2lm5_a", "aah17444") to readable names
- **PK Data Enrichment**: Fill gaps in pharmacokinetic information

### Implementation

#### `src/retrieval/pubchem_client.py` (NEW)
- `get_protein_label(protein_id)`: Gets human-readable label for a protein ID
  - Tries PDB API for PDB chain IDs (e.g., "2lm5_a")
  - Tries UniProt API for UniProt IDs
  - Falls back to original ID if no label found
- `enrich_protein_ids(protein_ids)`: Batch enrichment of protein IDs
- `get_compound_pk_data(pubchem_cid)`: Gets PK data for a compound (ADME, clearance, etc.)
- Rate limiting: 200ms between requests (5 req/sec max)

#### `src/retrieval/qlever_query.py`
- Integrated PubChem REST API enrichment for targets
- Extracts protein IDs from target URIs/labels
- Enriches with human-readable labels
- Updates target labels in-place

### API Endpoints Used

1. **PDB API** (`https://data.rcsb.org/rest/v1/core/entry/{pdb_id}`)
   - For PDB chain IDs (e.g., "2lm5_a" → "Angiotensin-converting enzyme (PDB: 2LM5)")

2. **UniProt API** (`https://www.uniprot.org/uniprot/{id}.json`)
   - For UniProt IDs (e.g., "P12821" → "Angiotensin-converting enzyme")

3. **PubChem REST API** (`https://pubchem.ncbi.nlm.nih.gov/rest/pug`)
   - For compound properties and PK data

## 3. Usage

### Enzyme-Action Mapping

The system now properly maps enzyme actions:

```python
from src.retrieval.duckdb_query import DuckDBClient

db = DuckDBClient('data/duckdb')
enzymes = db.get_drug_enzymes("fluconazole")

# Returns:
# {
#   "enzymes": ["Cytochrome P450 2C19", "Cytochrome P450 2C9", ...],
#   "enzyme_actions": ["inhibitor", "inhibitor", ...],  # Flat list
#   "enzyme_action_map": [  # Structured mapping (preferred)
#     {"enzyme": "Cytochrome P450 2C19", "actions": ["inhibitor"]},
#     {"enzyme": "Cytochrome P450 2C9", "actions": ["inhibitor"]},
#     ...
#   ]
# }
```

### Protein Label Enrichment

Protein labels are automatically enriched when querying targets:

```python
from src.retrieval.qlever_query import get_mechanistic

result = get_mechanistic("lisinopril", "spironolactone")
# Targets now include human-readable labels:
# ["Angiotensin-converting enzyme (PDB: 1N8E)", "Mineralocorticoid receptor (PDB: 1RA7)", ...]
```

## 4. Next Steps

### To Rebuild DrugBank Parquet with Enzyme Mapping

```bash
cd /home/pranjul/mydata/INFERMed
python scripts/build_parquets.py
# This will rebuild drugbank.parquet with enzyme_action_map column
```

### To Test

```python
# Test enzyme-action mapping
from src.retrieval.duckdb_query import DuckDBClient
db = DuckDBClient('data/duckdb')
enzymes = db.get_drug_enzymes("fluconazole")
print(enzymes["enzyme_action_map"])

# Test protein label enrichment
from src.retrieval.qlever_query import get_mechanistic
result = get_mechanistic("warfarin", "fluconazole")
print(result["targets_a"])  # Should show human-readable labels
```

## 5. Notes

- **Rate Limiting**: PubChem REST API has rate limits (5 req/sec). The code includes rate limiting.
- **Caching**: Uses `@lru_cache` for protein labels to avoid redundant API calls.
- **Fallback**: If PubChem API fails, falls back to original protein IDs.
- **Backward Compatibility**: Old parquet files without `enzyme_action_map` still work (falls back to flat list).

