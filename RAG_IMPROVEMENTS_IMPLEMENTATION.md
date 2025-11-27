# RAG Improvements Implementation: Semantic Search & Relevance Scoring

## Overview

This document describes the implementation of semantic search with embeddings and relevance scoring/ranking for the INFERMed RAG system.

## Implementation Date
- Started: After v1.0.0-stable tag
- Branch: `feature/rag-improvements`

## Components Added

### 1. Semantic Search Module (`src/retrieval/semantic_search.py`)

**Purpose**: Enable semantic similarity search for drugs and side effects using embeddings.

**Features**:
- Drug name embedding and similarity search
- Side effect semantic search
- Vector index management with caching
- Graceful degradation if dependencies unavailable

**Key Classes**:
- `SemanticSearcher`: Main class for semantic search operations
  - `build_drug_index()`: Build embedding index for drug names
  - `search_similar_drugs()`: Find similar drugs using cosine similarity
  - `build_side_effect_index()`: Build embedding index for side effects
  - `search_similar_side_effects()`: Find similar side effects

**Dependencies**:
- `sentence-transformers`: For generating embeddings
- `numpy`: For vector operations
- Default model: `all-MiniLM-L6-v2` (lightweight, fast)

**Caching**:
- Drug index cached in `data/cache/embeddings/drug_index.pkl`
- Side effect index cached in `data/cache/embeddings/side_effect_index.pkl`
- Indexes are built once and reused across queries

### 2. Relevance Scoring Module (`src/utils/relevance_scoring.py`)

**Purpose**: Score and rank retrieved evidence by relevance to the query.

**Features**:
- Multi-factor scoring (PRR, canonical interactions, overlaps, risk flags)
- Ranking functions for different evidence types
- Hybrid search result merging
- Relevance filtering

**Key Functions**:
- `score_evidence_item()`: Score a single evidence item
  - Canonical interactions: +10.0
  - High PRR (>2.0): +5.0
  - Pathway/target overlaps: +2-3.0
  - Enzyme interactions: +1.5-4.0
  - Risk flags (DILI, DICT, DIQT): +0.5-2.0
  - Semantic similarity: +0.5-1.0

- `score_and_rank_side_effects()`: Score and rank side effects
- `score_and_rank_pathways()`: Score and rank pathways
- `score_and_rank_targets()`: Score and rank targets
- `merge_and_rerank_evidence()`: Combine keyword + semantic results

### 3. RAG Pipeline Integration (`src/llm/rag_pipeline.py`)

**Changes**:
- Version bumped to 5 (invalidates old caches)
- Integrated semantic search for drug name matching
- Added relevance scoring for all retrieved evidence
- Applied ranking before top-K truncation

**Flow**:
1. Initialize semantic searcher (if available)
2. Build drug index (cached, one-time operation)
3. Retrieve evidence with expanded top-K (2x for ranking)
4. Apply semantic search fallback if exact match fails
5. Score all retrieved evidence by relevance
6. Rank and apply top-K truncation
7. Include semantic search in sources attribution

**Graceful Degradation**:
- If `sentence-transformers` not installed, semantic search is disabled
- System continues with keyword-only retrieval
- No errors raised, just logged warnings

## Configuration

### Environment Variables

```bash
# Embedding model (default: all-MiniLM-L6-v2)
export EMBEDDING_MODEL="all-MiniLM-L6-v2"

# Semantic similarity threshold (default: 0.5)
export SEMANTIC_SIMILARITY_THRESHOLD="0.6"
```

### Model Options

- `all-MiniLM-L6-v2`: Fast, lightweight (default)
- `all-mpnet-base-v2`: Better quality, slower
- `paraphrase-multilingual-MiniLM-L12-v2`: Multilingual support

## Usage

### Automatic (Integrated)

The semantic search and relevance scoring are automatically used in the RAG pipeline:

```python
from src.llm.rag_pipeline import run_rag

result = run_rag("warfarin", "aspirin", mode="Doctor")
# Semantic search and relevance scoring applied automatically
```

### Manual Usage

```python
from src.retrieval.semantic_search import SemanticSearcher
from src.utils.relevance_scoring import score_and_rank_side_effects

# Initialize searcher
searcher = SemanticSearcher()

# Build index
drug_names = ["warfarin", "aspirin", "ibuprofen", ...]
searcher.build_drug_index(drug_names)

# Search for similar drugs
similar = searcher.search_similar_drugs("coumadin", top_k=5)
# Returns: [("warfarin", 0.95), ("aspirin", 0.72), ...]

# Score evidence
scored = score_and_rank_side_effects(
    side_effects=["bleeding", "bruising"],
    query_context={"drug_a": "warfarin", "prr_pair": 2.5},
    prr_data={"bleeding": 3.2, "bruising": 1.8}
)
# Returns: [("bleeding", 7.5), ("bruising", 2.3)]
```

## Performance Impact

### Index Building
- **First run**: ~30-60 seconds for 10,000 drugs (one-time)
- **Subsequent runs**: <1 second (loaded from cache)
- **Memory**: ~50-100 MB for 10,000 drug embeddings

### Query Time
- **Semantic search**: +50-200ms per query (if enabled)
- **Relevance scoring**: +10-50ms per query
- **Overall impact**: +60-250ms per query (acceptable trade-off)

### Cache Benefits
- Drug index: Built once, reused indefinitely
- Side effect index: Built on-demand, cached
- Embeddings persist across sessions

## Testing

### Test Semantic Search

```python
# Test drug similarity
searcher = SemanticSearcher()
searcher.build_drug_index(["warfarin", "coumadin", "aspirin"])
results = searcher.search_similar_drugs("warfarin", top_k=3)
assert "coumadin" in [r[0] for r in results]  # Should find synonym
```

### Test Relevance Scoring

```python
from src.utils.relevance_scoring import score_evidence_item

item = {
    "prr": 2.5,
    "canonical_interaction": True,
    "pathway_overlap": True
}
score = score_evidence_item(item, {"drug_a": "warfarin"})
assert score > 10.0  # Should be high due to canonical + PRR
```

## Benefits

1. **Better Drug Matching**: Finds synonyms and related drugs (e.g., "warfarin" ↔ "coumadin")
2. **Relevance Ranking**: Most important evidence appears first
3. **Improved Recall**: Semantic search finds drugs not in exact match
4. **Better Context Quality**: LLM receives most relevant evidence first

## Limitations

1. **First Query Latency**: Index building on first use
2. **Memory Usage**: Embeddings require RAM (manageable for typical datasets)
3. **Model Dependency**: Requires `sentence-transformers` package
4. **Threshold Tuning**: Similarity threshold may need adjustment per use case

## Next Steps

1. ✅ Semantic search implemented
2. ✅ Relevance scoring implemented
3. 🔄 Query expansion (next phase)
4. 🔄 Re-ranking with cross-encoders (future)
5. 🔄 Hybrid search optimization (future)

## Rollback

If issues occur, rollback to v1.0.0-stable:

```bash
git checkout main
git reset --hard v1.0.0-stable
```

## Dependencies Added

- `sentence-transformers>=2.2.0`
- `numpy>=1.24.0`

Install with:
```bash
pip install sentence-transformers numpy
```

