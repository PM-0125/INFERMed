# src/utils/adaptive_retrieval.py
# -*- coding: utf-8 -*-
"""
Adaptive retrieval module for dynamic top-K selection.

This module provides functions to adaptively adjust retrieval size
based on result quality and relevance.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

# Default thresholds
DEFAULT_MIN_RELEVANCE = 0.3
DEFAULT_MIN_RESULTS = 5
DEFAULT_MAX_EXPANSION_FACTOR = 4


def calculate_result_quality(
    results: List[Any],
    score_extractor: Optional[Callable[[Any], float]] = None
) -> Dict[str, float]:
    """
    Calculate quality metrics for retrieved results.
    
    Args:
        results: List of results (can be tuples of (item, score) or items with scores)
        score_extractor: Optional function to extract score from result item
        
    Returns:
        Dictionary with quality metrics
    """
    if not results:
        return {
            "count": 0,
            "avg_score": 0.0,
            "max_score": 0.0,
            "min_score": 0.0,
            "quality_score": 0.0,
        }
    
    # Extract scores
    scores = []
    for result in results:
        if isinstance(result, (tuple, list)) and len(result) >= 2:
            score = float(result[1])
        elif score_extractor:
            score = score_extractor(result)
        else:
            # Try to get score attribute
            score = getattr(result, "score", 1.0)
        scores.append(score)
    
    if not scores:
        return {
            "count": len(results),
            "avg_score": 0.0,
            "max_score": 0.0,
            "min_score": 0.0,
            "quality_score": 0.0,
        }
    
    avg_score = sum(scores) / len(scores)
    max_score = max(scores)
    min_score = min(scores)
    
    # Quality score: combination of average and max, weighted by count
    quality_score = (avg_score * 0.6 + max_score * 0.4) * min(1.0, len(results) / 10.0)
    
    return {
        "count": len(results),
        "avg_score": avg_score,
        "max_score": max_score,
        "min_score": min_score,
        "quality_score": quality_score,
    }


def adaptive_retrieve(
    query: str,
    retrieve_fn: Callable[[str, int], List[Any]],
    initial_k: int = 10,
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
    min_results: int = DEFAULT_MIN_RESULTS,
    max_k: Optional[int] = None,
    score_extractor: Optional[Callable[[Any], float]] = None
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    Adaptively retrieve results, expanding if quality is low.
    
    Args:
        query: Query string
        retrieve_fn: Function that takes (query, k) and returns results
        initial_k: Initial number of results to retrieve
        min_relevance: Minimum average relevance threshold
        min_results: Minimum number of results required
        max_k: Maximum k to retrieve (None = no limit)
        score_extractor: Optional function to extract scores from results
        
    Returns:
        Tuple of (results, metadata) where metadata contains retrieval stats
    """
    metadata = {
        "initial_k": initial_k,
        "final_k": initial_k,
        "expanded": False,
        "expansion_factor": 1.0,
        "quality_before": None,
        "quality_after": None,
    }
    
    # Initial retrieval
    results = retrieve_fn(query, initial_k)
    quality = calculate_result_quality(results, score_extractor)
    metadata["quality_before"] = quality
    
    # Check if we need to expand
    should_expand = False
    
    # Check relevance threshold
    if quality["avg_score"] < min_relevance:
        should_expand = True
        LOG.debug(f"Low average relevance ({quality['avg_score']:.2f} < {min_relevance}), expanding")
    
    # Check minimum results
    if quality["count"] < min_results:
        should_expand = True
        LOG.debug(f"Insufficient results ({quality['count']} < {min_results}), expanding")
    
    # Expand if needed
    if should_expand:
        expansion_factor = 2.0
        expanded_k = int(initial_k * expansion_factor)
        
        # Respect max_k if set
        if max_k is not None:
            expanded_k = min(expanded_k, max_k)
        
        # Only expand if we're not already at max
        if expanded_k > initial_k:
            expanded_results = retrieve_fn(query, expanded_k)
            expanded_quality = calculate_result_quality(expanded_results, score_extractor)
            
            # Use expanded results if they're better
            if expanded_quality["quality_score"] > quality["quality_score"]:
                results = expanded_results
                metadata["final_k"] = expanded_k
                metadata["expanded"] = True
                metadata["expansion_factor"] = expansion_factor
                metadata["quality_after"] = expanded_quality
                LOG.debug(f"Expanded retrieval from {initial_k} to {expanded_k}")
            else:
                metadata["quality_after"] = quality
    
    return results, metadata


def adaptive_retrieve_with_fallback(
    query: str,
    primary_retrieve_fn: Callable[[str, int], List[Any]],
    fallback_retrieve_fn: Optional[Callable[[str, int], List[Any]]] = None,
    initial_k: int = 10,
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
    min_results: int = DEFAULT_MIN_RESULTS,
    max_k: Optional[int] = None,
    score_extractor: Optional[Callable[[Any], float]] = None
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    Adaptively retrieve with fallback to secondary source.
    
    Args:
        query: Query string
        primary_retrieve_fn: Primary retrieval function
        fallback_retrieve_fn: Optional fallback retrieval function
        initial_k: Initial k
        min_relevance: Minimum relevance threshold
        min_results: Minimum results required
        max_k: Maximum k
        score_extractor: Score extraction function
        
    Returns:
        Tuple of (results, metadata)
    """
    # Try primary source
    results, metadata = adaptive_retrieve(
        query,
        primary_retrieve_fn,
        initial_k=initial_k,
        min_relevance=min_relevance,
        min_results=min_results,
        max_k=max_k,
        score_extractor=score_extractor
    )
    
    metadata["used_fallback"] = False
    
    # If primary fails and fallback available, try fallback
    if not results and fallback_retrieve_fn:
        LOG.debug(f"Primary retrieval failed, trying fallback for '{query}'")
        fallback_results, fallback_metadata = adaptive_retrieve(
            query,
            fallback_retrieve_fn,
            initial_k=initial_k,
            min_relevance=min_relevance,
            min_results=min_results,
            max_k=max_k,
            score_extractor=score_extractor
        )
        
        if fallback_results:
            results = fallback_results
            metadata["used_fallback"] = True
            metadata["fallback_metadata"] = fallback_metadata
    
    return results, metadata

