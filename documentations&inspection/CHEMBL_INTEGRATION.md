# ChEMBL REST API Integration

## Overview

ChEMBL integration provides additional PK/PD data to fill gaps in the current system:
- **Enzyme Strength Classification**: Weak/moderate/strong inhibitor classification based on Ki/IC50 values
- **Transporter Data**: P-gp, OATP, OCT, OAT, MATE, BCRP interactions
- **Pathway Information**: Biological pathway associations
- **Cross-Validation**: Verify DrugBank enzyme data against ChEMBL

## Configuration

Add to your `.env` file:

```bash
# Enable ChEMBL enrichment (optional, defaults to false)
CHEMBL_ENABLED=true

# ChEMBL API timeout (seconds)
CHEMBL_TIMEOUT=10
```

## Usage

The ChEMBL client is automatically integrated into `get_mechanistic()` when `CHEMBL_ENABLED=true`.

### Example

```python
from src.retrieval.qlever_query import get_mechanistic

result = get_mechanistic('fluconazole', 'warfarin')

# Check ChEMBL enrichment
if 'chembl_enrichment' in result:
    chembl_a = result['chembl_enrichment']['a']
    print(f"Enzyme strength: {chembl_a.get('enzyme_strength')}")
    print(f"ChEMBL validation: {chembl_a.get('chembl_validation')}")
```

## API Functions

### `get_enzyme_interactions(compound_name, enzyme_name=None)`

Get enzyme interactions with potency data (Ki, IC50, EC50).

**Returns**: List of dicts with:
- `enzyme`: Enzyme name (e.g., "cyp3a4")
- `action`: Action type (strong_inhibitor, moderate_inhibitor, weak_inhibitor, substrate)
- `potency_type`: "Ki", "IC50", or "EC50"
- `potency_value`: Numeric value
- `potency_units`: Units (typically "nM" or "μM")
- `target_name`: Full target name from ChEMBL

### `get_transporter_data(compound_name)`

Get transporter interactions (P-gp, OATP, etc.).

**Returns**: List of dicts with:
- `transporter`: Transporter name
- `action`: "substrate" or "inhibitor"

### `get_pathway_data(compound_name)`

Get pathway associations.

**Returns**: List of pathway names (strings)

### `enrich_mechanistic_data(drug_name, enzymes)`

Enrich enzyme data with ChEMBL potency and validation.

**Returns**: Dict with:
- `enzymes`: Original enzyme dict
- `enzyme_strength`: Dict with "strong", "moderate", "weak" lists
- `chembl_validation`: Dict with "found", "matches", "mismatches"

## Enzyme Strength Classification

Based on Ki/IC50 values:
- **Strong inhibitor**: < 1 μM
- **Moderate inhibitor**: 1-10 μM
- **Weak inhibitor**: > 10 μM
- **Substrate**: EC50 values

## Limitations

1. **API Rate Limits**: ChEMBL REST API has rate limits. The client uses caching (`@lru_cache`) to minimize requests.
2. **Name Matching**: Compound name matching may not always find the correct ChEMBL molecule. Manual verification may be needed.
3. **Network Dependency**: Requires internet connection to ChEMBL servers.

## Future Enhancements

1. **Batch Queries**: Query multiple compounds at once
2. **Offline Cache**: Cache ChEMBL data locally for offline use
3. **Better Name Matching**: Use PubChem CID → ChEMBL mapping for more accurate matching
4. **Transporter Strength**: Add potency-based classification for transporters

