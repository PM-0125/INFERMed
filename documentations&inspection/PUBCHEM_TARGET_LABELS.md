# PubChem Target Label Enrichment

## Problem

Target IDs from PubChem RDF are PDB chain identifiers like `1DE9_A`, `1JY1_A`, etc., which are not human-readable.

## Solution

The system now **always** enriches target IDs with human-readable labels using:

1. **PubChem RDF REST API** (primary method)
2. **RCSB PDB REST API** (fallback)
3. **UniProt API** (for UniProt IDs)

## How It Works

### PDB Chain ID Format

| Raw ID | Meaning | Example |
|--------|---------|---------|
| `1DE9_A` | PDB entry 1DE9, chain A | Dihydrofolate reductase (Thermus thermophilus) |
| `1JY1_A` | PDB entry 1JY1, chain A | Glucocorticoid receptor ligand-binding domain |
| `1N8E_E` | PDB entry 1N8E, chain E | Cytochrome c oxidase subunit I |

### Enrichment Process

1. **Extract PDB ID and chain** from target URI/label
2. **Query PubChem RDF REST API**: `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/{pdb_chain_id}/rdf/`
3. **Parse RDF** to extract `rdfs:label` or `dc:title`
4. **Fallback to PDB API**: If PubChem RDF fails, query RCSB PDB REST API
5. **Return enriched label**: `"{Protein Name} (PDB: {pdb_id}{chain})"`

### Code Location

- **`src/retrieval/pubchem_client.py`**: `get_protein_label()` function
- **`src/retrieval/qlever_query.py`**: Always calls enrichment (not a fallback)

## Example Output

**Before:**
```
Targets: ['1DE9_A', '1JY1_A', '1N8E_E']
```

**After:**
```
Targets: [
  'Dihydrofolate reductase (Thermus thermophilus) (PDB: 1DE9A)',
  'Glucocorticoid receptor ligand-binding domain (PDB: 1JY1A)',
  'Cytochrome c oxidase subunit I (PDB: 1N8EE)'
]
```

## API Endpoints Used

1. **PubChem RDF REST API**
   - URL: `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/{id}/rdf/`
   - Format: RDF/XML or Turtle
   - Extracts: `rdfs:label` or `dc:title`

2. **RCSB PDB REST API**
   - URL: `https://data.rcsb.org/rest/v1/core/entry/{pdb_id}`
   - Format: JSON
   - Extracts: `struct.title`

3. **UniProt API** (for UniProt IDs)
   - URL: `https://www.uniprot.org/uniprot/{id}.json`
   - Format: JSON
   - Extracts: `proteinDescription.recommendedName.fullName.value`

## Rate Limiting

- **200ms delay** between requests (5 req/sec max)
- **Caching**: Uses `@lru_cache` to avoid redundant API calls
- **Timeout**: 10 seconds per request (configurable via `PUBCHEM_TIMEOUT`)

## Integration

The enrichment is **always** performed in `get_mechanistic()`:

```python
# ALWAYS enrich with PubChem REST API labels (not a fallback)
from src.retrieval import pubchem_client as pc
enriched_targets = pc.enrich_protein_ids(protein_ids)
# Update targets with enriched labels
```

## Testing

Run comprehensive tests:
```bash
python3 -c "
from src.retrieval import pubchem_client as pc
labels = [pc.get_protein_label(pid) for pid in ['1DE9_A', '1JY1_A', '1N8E_E']]
print(labels)
"
```

## Notes

- If enrichment fails, the original PDB chain ID is returned (graceful degradation)
- Labels are cached to minimize API calls
- Works for both PDB chain IDs and UniProt IDs
- Always runs (not conditional on other data availability)

