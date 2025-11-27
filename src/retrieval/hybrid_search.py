# src/retrieval/hybrid_search.py
# -*- coding: utf-8 -*-
"""
Hybrid search module combining keyword and semantic search.

This module provides functions to combine keyword-based retrieval
with semantic search for improved recall and precision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Callable

from src.utils.relevance_scoring import merge_and_rerank_evidence

LOG = logging.getLogger(__name__)

# Default weights for hybrid search
DEFAULT_KEYWORD_WEIGHT = 0.6
DEFAULT_SEMANTIC_WEIGHT = 0.4


def hybrid_search_drugs(
    query: str,
    keyword_search_fn: Callable[[str, int], List[Tuple[str, float]]],
    semantic_search_fn: Optional[Callable[[str, int, float], List[Tuple[str, float]]]],
    top_k: int = 10,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
    min_semantic_threshold: float = 0.6
) -> List[Tuple[str, float]]:
    """
    Perform hybrid search combining keyword and semantic results.
    
    Args:
        query: Drug name or query string
        keyword_search_fn: Function that takes (query, top_k) and returns [(item, score), ...]
        semantic_search_fn: Optional function that takes (query, top_k, threshold) and returns [(item, score), ...]
        top_k: Number of results to return
        keyword_weight: Weight for keyword search scores (0-1)
        semantic_weight: Weight for semantic search scores (0-1)
        min_semantic_threshold: Minimum similarity threshold for semantic search
        
    Returns:
        List of (item, combined_score) tuples, sorted by score (descending)
    """
    # Perform keyword search
    keyword_results = keyword_search_fn(query, top_k * 2)  # Get more for merging
    
    # Perform semantic search if available
    semantic_results = []
    if semantic_search_fn is not None:
        try:
            semantic_results = semantic_search_fn(query, top_k * 2, min_semantic_threshold)
        except Exception as e:
            LOG.warning(f"Semantic search failed: {e}")
            semantic_results = []
    
    # Merge and rerank
    if semantic_results:
        merged = merge_and_rerank_evidence(
            keyword_results,
            semantic_results,
            keyword_weight=keyword_weight,
            semantic_weight=semantic_weight
        )
    else:
        # No semantic results, just use keyword results
        merged = keyword_results
    
    # Return top-k
    return merged[:top_k]


def hybrid_search_side_effects(
    query: str,
    keyword_search_fn: Callable[[str, int], List[str]],
    semantic_search_fn: Optional[Callable[[str, int, float], List[Tuple[str, float]]]],
    top_k: int = 10,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
    min_semantic_threshold: float = 0.6
) -> List[Tuple[str, float]]:
    """
    Perform hybrid search for side effects.
    
    Args:
        query: Side effect name or query
        keyword_search_fn: Function that returns list of side effect names
        semantic_search_fn: Optional semantic search function
        top_k: Number of results
        keyword_weight: Weight for keyword search
        semantic_weight: Weight for semantic search
        min_semantic_threshold: Minimum similarity threshold
        
    Returns:
        List of (side_effect, combined_score) tuples
    """
    # Keyword search - convert to (item, score) format
    keyword_items = keyword_search_fn(query, top_k * 2)
    keyword_results = [(item, 1.0) for item in keyword_items]  # Default score of 1.0
    
    # Semantic search
    semantic_results = []
    if semantic_search_fn is not None:
        try:
            semantic_results = semantic_search_fn(query, top_k * 2, min_semantic_threshold)
        except Exception as e:
            LOG.warning(f"Semantic search failed: {e}")
            semantic_results = []
    
    # Merge and rerank
    if semantic_results:
        merged = merge_and_rerank_evidence(
            keyword_results,
            semantic_results,
            keyword_weight=keyword_weight,
            semantic_weight=semantic_weight
        )
    else:
        merged = keyword_results
    
    return merged[:top_k]


def adaptive_hybrid_search(
    query: str,
    keyword_search_fn: Callable[[str, int], List[Tuple[str, float]]],
    semantic_search_fn: Optional[Callable[[str, int, float], List[Tuple[str, float]]]],
    initial_k: int = 10,
    min_relevance_threshold: float = 0.5,
    max_k: int = 50,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT
) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
    """
    Perform adaptive hybrid search that adjusts retrieval size based on result quality.
    
    Args:
        query: Query string
        keyword_search_fn: Keyword search function
        semantic_search_fn: Optional semantic search function
        initial_k: Initial number of results to retrieve
        min_relevance_threshold: Minimum relevance score threshold
        max_k: Maximum number of results to retrieve
        keyword_weight: Weight for keyword search
        semantic_weight: Weight for semantic search
        
    Returns:
        Tuple of (results, metadata) where metadata contains search statistics
    """
    metadata = {
        "initial_k": initial_k,
        "final_k": initial_k,
        "expanded": False,
        "keyword_results_count": 0,
        "semantic_results_count": 0,
    }
    
    # Initial search
    results = hybrid_search_drugs(
        query,
        keyword_search_fn,
        semantic_search_fn,
        top_k=initial_k,
        keyword_weight=keyword_weight,
        semantic_weight=semantic_weight
    )
    
    metadata["keyword_results_count"] = len(keyword_search_fn(query, initial_k * 2))
    if semantic_search_fn:
        try:
            semantic_res = semantic_search_fn(query, initial_k * 2, 0.5)
            metadata["semantic_results_count"] = len(semantic_res)
        except Exception:
            pass
    
    # Check result quality
    if results:
        # Calculate average relevance
        avg_score = sum(score for _, score in results) / len(results)
        max_score = max(score for _, score in results) if results else 0.0
        
        # If average relevance is low and we haven't hit max_k, expand search
        if avg_score < min_relevance_threshold and initial_k < max_k:
            expanded_k = min(initial_k * 2, max_k)
            expanded_results = hybrid_search_drugs(
                query,
                keyword_search_fn,
                semantic_search_fn,
                top_k=expanded_k,
                keyword_weight=keyword_weight,
                semantic_weight=semantic_weight
            )
            
            if expanded_results:
                results = expanded_results
                metadata["final_k"] = expanded_k
                metadata["expanded"] = True
                metadata["avg_score_before"] = avg_score
                metadata["avg_score_after"] = sum(score for _, score in expanded_results) / len(expanded_results)
    else:
        # No results, try expanding
        if initial_k < max_k:
            expanded_k = min(initial_k * 2, max_k)
            expanded_results = hybrid_search_drugs(
                query,
                keyword_search_fn,
                semantic_search_fn,
                top_k=expanded_k,
                keyword_weight=keyword_weight,
                semantic_weight=semantic_weight
            )
            
            if expanded_results:
                results = expanded_results
                metadata["final_k"] = expanded_k
                metadata["expanded"] = True
    
    return results, metadata

