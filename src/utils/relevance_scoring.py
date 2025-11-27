# src/utils/relevance_scoring.py
# -*- coding: utf-8 -*-
"""
Relevance scoring module for ranking retrieved evidence.

This module provides functions to score and rank evidence items by their
relevance to a drug interaction query.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


def score_evidence_item(
    item: Dict[str, Any],
    query_context: Dict[str, Any],
    item_type: str = "side_effect"
) -> float:
    """
    Score a single evidence item by relevance to the query.
    
    Args:
        item: Evidence item to score (side effect, pathway, target, etc.)
        query_context: Context about the query (drug pair, PRR, etc.)
        item_type: Type of evidence item ('side_effect', 'pathway', 'target', 'enzyme')
        
    Returns:
        Relevance score (higher = more relevant)
    """
    score = 0.0
    
    # Canonical interaction gets highest priority
    if item.get("canonical_interaction"):
        score += 10.0
    
    # PRR-based scoring (for side effects and interactions)
    prr = item.get("prr")
    if prr is not None:
        try:
            prr_val = float(prr)
            if prr_val > 2.0:
                score += 5.0  # Strong signal
            elif prr_val > 1.5:
                score += 2.0  # Moderate signal
            elif prr_val > 1.0:
                score += 0.5  # Weak signal
        except (ValueError, TypeError):
            pass
    
    # Pathway/target overlap scoring
    if item.get("pathway_overlap"):
        score += 3.0
    if item.get("target_overlap"):
        score += 2.0
    
    # Enzyme interaction scoring
    if item.get("enzyme_inhibition"):
        score += 4.0  # Inhibition is high relevance
    if item.get("enzyme_induction"):
        score += 3.0  # Induction is also high relevance
    if item.get("shared_substrate"):
        score += 1.5  # Shared substrate is moderate relevance
    
    # Frequency/count-based scoring (for FAERS data)
    count = item.get("count") or item.get("frequency")
    if count is not None:
        try:
            count_val = int(count)
            if count_val > 1000:
                score += 2.0
            elif count_val > 100:
                score += 1.0
            elif count_val > 10:
                score += 0.5
        except (ValueError, TypeError):
            pass
    
    # Risk flag scoring
    dili = item.get("dili_risk")
    if dili in ("high", "severe"):
        score += 2.0
    elif dili in ("medium", "moderate"):
        score += 1.0
    
    dict_risk = item.get("dict_risk")
    if dict_risk in ("severe", "high"):
        score += 2.0
    elif dict_risk in ("moderate", "medium"):
        score += 1.0
    
    diqt = item.get("diqt_score")
    if diqt is not None:
        try:
            diqt_val = float(diqt)
            if diqt_val > 0.7:
                score += 1.5
            elif diqt_val > 0.4:
                score += 0.5
        except (ValueError, TypeError):
            pass
    
    # Semantic similarity bonus (if available)
    semantic_score = item.get("semantic_similarity")
    if semantic_score is not None:
        try:
            sim_val = float(semantic_score)
            if sim_val > 0.8:
                score += 1.0
            elif sim_val > 0.6:
                score += 0.5
        except (ValueError, TypeError):
            pass
    
    # Pair-specific scoring (if item is specific to the drug pair)
    if item.get("pair_specific", False):
        score += 1.0
    
    return score


def score_and_rank_side_effects(
    side_effects: List[str],
    query_context: Dict[str, Any],
    prr_data: Optional[Dict[str, float]] = None,
    semantic_scores: Optional[Dict[str, float]] = None
) -> List[Tuple[str, float]]:
    """
    Score and rank side effects by relevance.
    
    Args:
        side_effects: List of side effect names
        query_context: Query context (drug pair, etc.)
        prr_data: Optional dict mapping side effect to PRR value
        semantic_scores: Optional dict mapping side effect to semantic similarity
        
    Returns:
        List of (side_effect, score) tuples, sorted by score (descending)
    """
    scored = []
    
    for se in side_effects:
        item = {
            "name": se,
            "prr": prr_data.get(se) if prr_data else None,
            "semantic_similarity": semantic_scores.get(se) if semantic_scores else None,
        }
        score = score_evidence_item(item, query_context, item_type="side_effect")
        scored.append((se, score))
    
    # Sort by score (descending)
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def score_and_rank_pathways(
    pathways: List[str],
    query_context: Dict[str, Any],
    overlap_pathways: Optional[List[str]] = None
) -> List[Tuple[str, float]]:
    """
    Score and rank pathways by relevance.
    
    Args:
        pathways: List of pathway names
        query_context: Query context
        overlap_pathways: List of pathways that overlap between drugs
        
    Returns:
        List of (pathway, score) tuples, sorted by score
    """
    overlap_set = set(overlap_pathways or [])
    scored = []
    
    for pathway in pathways:
        item = {
            "name": pathway,
            "pathway_overlap": pathway in overlap_set,
        }
        score = score_evidence_item(item, query_context, item_type="pathway")
        scored.append((pathway, score))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def score_and_rank_targets(
    targets: List[str],
    query_context: Dict[str, Any],
    overlap_targets: Optional[List[str]] = None
) -> List[Tuple[str, float]]:
    """
    Score and rank targets by relevance.
    
    Args:
        targets: List of target names/IDs
        query_context: Query context
        overlap_targets: List of targets that overlap between drugs
        
    Returns:
        List of (target, score) tuples, sorted by score
    """
    overlap_set = set(overlap_targets or [])
    scored = []
    
    for target in targets:
        item = {
            "name": target,
            "target_overlap": target in overlap_set,
        }
        score = score_evidence_item(item, query_context, item_type="target")
        scored.append((target, score))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def apply_relevance_filter(
    items: List[Any],
    scores: List[float],
    min_score: float = 0.0,
    top_k: Optional[int] = None
) -> List[Any]:
    """
    Filter and optionally truncate items by relevance score.
    
    Args:
        items: List of items to filter
        scores: List of corresponding scores
        min_score: Minimum score threshold
        top_k: Optional maximum number of items to return
        
    Returns:
        Filtered list of items
    """
    # Combine items and scores
    item_scores = list(zip(items, scores))
    
    # Filter by minimum score
    filtered = [(item, score) for item, score in item_scores if score >= min_score]
    
    # Sort by score (descending)
    filtered.sort(key=lambda x: x[1], reverse=True)
    
    # Apply top-k if specified
    if top_k is not None:
        filtered = filtered[:top_k]
    
    # Return just the items (without scores)
    return [item for item, score in filtered]


def merge_and_rerank_evidence(
    keyword_results: List[Tuple[str, float]],
    semantic_results: List[Tuple[str, float]],
    keyword_weight: float = 0.6,
    semantic_weight: float = 0.4
) -> List[Tuple[str, float]]:
    """
    Merge keyword and semantic search results with weighted combination.
    
    Args:
        keyword_results: List of (item, keyword_score) tuples
        semantic_results: List of (item, semantic_score) tuples
        keyword_weight: Weight for keyword scores
        semantic_weight: Weight for semantic scores
        
    Returns:
        Merged and reranked list of (item, combined_score) tuples
    """
    # Normalize weights
    total_weight = keyword_weight + semantic_weight
    if total_weight > 0:
        keyword_weight /= total_weight
        semantic_weight /= total_weight
    
    # Create dicts for easy lookup
    keyword_dict = dict(keyword_results)
    semantic_dict = dict(semantic_results)
    
    # Get all unique items
    all_items = set(keyword_dict.keys()) | set(semantic_dict.keys())
    
    # Combine scores
    combined = []
    for item in all_items:
        kw_score = keyword_dict.get(item, 0.0)
        sem_score = semantic_dict.get(item, 0.0)
        combined_score = (kw_score * keyword_weight) + (sem_score * semantic_weight)
        combined.append((item, combined_score))
    
    # Sort by combined score
    combined.sort(key=lambda x: x[1], reverse=True)
    return combined

