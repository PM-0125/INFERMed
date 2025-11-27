# src/utils/query_expansion.py
# -*- coding: utf-8 -*-
"""
Query expansion module for drug name queries.

This module provides functions to expand drug queries with synonyms,
variations, and related terms to improve retrieval recall.
"""

from __future__ import annotations

import logging
import re
from typing import List, Set, Optional, Dict, Any

LOG = logging.getLogger(__name__)


def expand_drug_query(
    drug_name: str,
    synonyms: Optional[List[str]] = None,
    use_variations: bool = True,
    use_case_variants: bool = True
) -> List[str]:
    """
    Expand a drug query with synonyms and variations.
    
    Args:
        drug_name: Original drug name to expand
        synonyms: Optional list of synonyms from DrugBank or other sources
        use_variations: If True, generate common name variations
        use_case_variants: If True, include case variations
        
    Returns:
        List of expanded query terms (includes original)
    """
    if not drug_name or not drug_name.strip():
        return []
    
    expanded: Set[str] = set()
    original = drug_name.strip()
    
    # Always include original
    expanded.add(original)
    
    # Add synonyms if provided
    if synonyms:
        for synonym in synonyms:
            if synonym and synonym.strip():
                expanded.add(synonym.strip())
    
    # Generate case variations
    if use_case_variants:
        expanded.add(original.lower())
        expanded.add(original.upper())
        expanded.add(original.title())
        expanded.add(original.capitalize())
    
    # Generate common variations
    if use_variations:
        # Remove common suffixes/prefixes and add variations
        variations = _generate_name_variations(original)
        expanded.update(variations)
    
    # Remove empty strings and normalize
    expanded = {term for term in expanded if term}
    
    return sorted(list(expanded), key=lambda x: (x != original, x.lower()))


def _generate_name_variations(name: str) -> List[str]:
    """
    Generate common name variations.
    
    Args:
        name: Drug name
        
    Returns:
        List of variations
    """
    variations = []
    
    # Remove common prefixes/suffixes
    # e.g., "warfarin sodium" -> "warfarin"
    patterns_to_remove = [
        r'\s+sodium\s*$',
        r'\s+tablet\s*$',
        r'\s+capsule\s*$',
        r'\s+injection\s*$',
        r'\s+oral\s*$',
        r'\s+iv\s*$',
        r'\s+im\s*$',
    ]
    
    base_name = name
    for pattern in patterns_to_remove:
        base_name = re.sub(pattern, '', base_name, flags=re.IGNORECASE)
    
    if base_name != name:
        variations.append(base_name.strip())
    
    # Handle hyphenated names
    # e.g., "acetylsalicylic-acid" -> "acetylsalicylic acid"
    if '-' in name:
        variations.append(name.replace('-', ' '))
        variations.append(name.replace('-', ''))
    
    # Handle spaced names
    # e.g., "acetylsalicylic acid" -> "acetylsalicylic-acid"
    if ' ' in name:
        variations.append(name.replace(' ', '-'))
        variations.append(name.replace(' ', ''))
    
    # Remove numbers at the end (e.g., "drug123" -> "drug")
    match = re.match(r'^(.+?)(\d+)$', name)
    if match:
        variations.append(match.group(1))
    
    return [v for v in variations if v and v != name]


def expand_drug_pair_queries(
    drug_a: str,
    drug_b: str,
    synonyms_a: Optional[List[str]] = None,
    synonyms_b: Optional[List[str]] = None
) -> Dict[str, List[str]]:
    """
    Expand queries for a drug pair.
    
    Args:
        drug_a: First drug name
        drug_b: Second drug name
        synonyms_a: Synonyms for drug A
        synonyms_b: Synonyms for drug B
        
    Returns:
        Dictionary with 'drug_a' and 'drug_b' keys containing expanded terms
    """
    return {
        "drug_a": expand_drug_query(drug_a, synonyms_a),
        "drug_b": expand_drug_query(drug_b, synonyms_b),
    }


def get_best_match_from_expanded(
    expanded_terms: List[str],
    match_function: callable,
    max_results: int = 1
) -> List[str]:
    """
    Try expanded terms in order and return the first successful match.
    
    Args:
        expanded_terms: List of expanded query terms (ordered by priority)
        match_function: Function that takes a term and returns results (truthy if match found)
        max_results: Maximum number of results to return
        
    Returns:
        List of matched terms (up to max_results)
    """
    matched = []
    
    for term in expanded_terms:
        try:
            result = match_function(term)
            if result:
                matched.append(term)
                if len(matched) >= max_results:
                    break
        except Exception as e:
            LOG.debug(f"Match function failed for term '{term}': {e}")
            continue
    
    return matched


def merge_expanded_results(
    results_by_term: Dict[str, Any],
    merge_function: Optional[callable] = None
) -> Any:
    """
    Merge results from multiple expanded query terms.
    
    Args:
        results_by_term: Dictionary mapping query terms to their results
        merge_function: Optional function to merge results (default: union for lists)
        
    Returns:
        Merged results
    """
    if not results_by_term:
        return []
    
    if merge_function:
        # Use custom merge function
        merged = None
        for term, result in results_by_term.items():
            if merged is None:
                merged = result
            else:
                merged = merge_function(merged, result)
        return merged
    
    # Default: union for lists/sets
    all_results = []
    seen = set()
    
    for term, result in results_by_term.items():
        if isinstance(result, (list, tuple)):
            for item in result:
                # Use string representation for deduplication
                item_str = str(item)
                if item_str not in seen:
                    seen.add(item_str)
                    all_results.append(item)
        elif isinstance(result, set):
            for item in result:
                item_str = str(item)
                if item_str not in seen:
                    seen.add(item_str)
                    all_results.append(item)
        else:
            # For non-iterable results, just append
            all_results.append(result)
    
    return all_results


def expand_with_semantic_similarity(
    drug_name: str,
    semantic_searcher: Any,
    top_k: int = 3,
    threshold: float = 0.7
) -> List[str]:
    """
    Expand query using semantic search to find similar drug names.
    
    Args:
        drug_name: Original drug name
        semantic_searcher: SemanticSearcher instance
        top_k: Number of similar drugs to retrieve
        threshold: Minimum similarity threshold
        
    Returns:
        List of expanded terms (original + similar drugs)
    """
    expanded = [drug_name]
    
    if semantic_searcher is None:
        return expanded
    
    try:
        similar = semantic_searcher.search_similar_drugs(
            drug_name, top_k=top_k, threshold=threshold
        )
        
        for similar_drug, score in similar:
            if similar_drug != drug_name:
                expanded.append(similar_drug)
        
    except Exception as e:
        LOG.warning(f"Semantic expansion failed for '{drug_name}': {e}")
    
    return expanded


def create_expanded_query_context(
    drug_a: str,
    drug_b: str,
    synonyms_a: Optional[List[str]] = None,
    synonyms_b: Optional[List[str]] = None,
    semantic_searcher: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Create a comprehensive expanded query context.
    
    Args:
        drug_a: First drug name
        drug_b: Second drug name
        synonyms_a: Synonyms for drug A
        synonyms_b: Synonyms for drug B
        semantic_searcher: Optional semantic searcher for similarity expansion
        
    Returns:
        Dictionary with expanded query information
    """
    # Basic expansion
    expanded_a = expand_drug_query(drug_a, synonyms_a)
    expanded_b = expand_drug_query(drug_b, synonyms_b)
    
    # Semantic expansion if available
    if semantic_searcher:
        semantic_a = expand_with_semantic_similarity(drug_a, semantic_searcher)
        semantic_b = expand_with_semantic_similarity(drug_b, semantic_searcher)
        
        # Merge semantic results
        expanded_a = list(set(expanded_a + semantic_a))
        expanded_b = list(set(expanded_b + semantic_b))
    
    return {
        "original": {"drug_a": drug_a, "drug_b": drug_b},
        "expanded": {
            "drug_a": expanded_a,
            "drug_b": expanded_b,
        },
        "expansion_methods": {
            "synonyms": synonyms_a is not None or synonyms_b is not None,
            "variations": True,
            "semantic": semantic_searcher is not None,
        },
    }

