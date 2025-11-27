# src/utils/context_filtering.py
# -*- coding: utf-8 -*-
"""
Query-to-context relevance filtering module.

This module provides functions to filter context items by their
relevance to the specific query.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.relevance_scoring import score_evidence_item

LOG = logging.getLogger(__name__)

# Default threshold
DEFAULT_RELEVANCE_THRESHOLD = 0.1


def filter_context_by_relevance(
    context: Dict[str, Any],
    query: str,
    query_context: Dict[str, Any],
    min_relevance: float = DEFAULT_RELEVANCE_THRESHOLD,
    preserve_structure: bool = True
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Filter context items by relevance to query.
    
    Args:
        context: Full context dictionary
        query: Query string (drug pair or specific question)
        query_context: Query context (drug names, PRR, etc.)
        min_relevance: Minimum relevance score threshold
        preserve_structure: If True, preserve context structure; if False, return flat filtered items
        
    Returns:
        Tuple of (filtered_context, metadata) where metadata contains filtering stats
    """
    metadata = {
        "items_before": 0,
        "items_after": 0,
        "filtered_sections": [],
    }
    
    filtered_context = {}
    
    # Filter different sections of context
    if "signals" in context:
        filtered_context["signals"] = {}
        signals = context["signals"]
        
        # Filter tabular signals (side effects)
        if "tabular" in signals:
            tabular = signals["tabular"].copy()
            side_effects_a = tabular.get("side_effects_a", [])
            side_effects_b = tabular.get("side_effects_b", [])
            
            metadata["items_before"] += len(side_effects_a) + len(side_effects_b)
            
            # Score and filter side effects
            filtered_se_a = _filter_items_by_relevance(
                side_effects_a, query_context, "side_effect", min_relevance
            )
            filtered_se_b = _filter_items_by_relevance(
                side_effects_b, query_context, "side_effect", min_relevance
            )
            
            tabular["side_effects_a"] = filtered_se_a
            tabular["side_effects_b"] = filtered_se_b
            filtered_context["signals"]["tabular"] = tabular
            
            metadata["items_after"] += len(filtered_se_a) + len(filtered_se_b)
            if len(filtered_se_a) < len(side_effects_a) or len(filtered_se_b) < len(side_effects_b):
                metadata["filtered_sections"].append("side_effects")
        
        # Filter mechanistic evidence
        if "mechanistic" in signals:
            mech = signals["mechanistic"].copy()
            
            # Filter targets
            targets_a = mech.get("targets_a", [])
            targets_b = mech.get("targets_b", [])
            overlap_targets = mech.get("common_targets", []) or []
            
            metadata["items_before"] += len(targets_a) + len(targets_b)
            
            filtered_targets_a = _filter_items_by_relevance(
                targets_a, query_context, "target", min_relevance, overlap_items=overlap_targets
            )
            filtered_targets_b = _filter_items_by_relevance(
                targets_b, query_context, "target", min_relevance, overlap_items=overlap_targets
            )
            
            mech["targets_a"] = filtered_targets_a
            mech["targets_b"] = filtered_targets_b
            filtered_context["signals"]["mechanistic"] = mech
            
            metadata["items_after"] += len(filtered_targets_a) + len(filtered_targets_b)
            if len(filtered_targets_a) < len(targets_a) or len(filtered_targets_b) < len(targets_b):
                metadata["filtered_sections"].append("targets")
            
            # Filter pathways
            pathways_a = mech.get("pathways_a", [])
            pathways_b = mech.get("pathways_b", [])
            common_pathways = mech.get("common_pathways", []) or []
            
            metadata["items_before"] += len(pathways_a) + len(pathways_b)
            
            filtered_pathways_a = _filter_items_by_relevance(
                pathways_a, query_context, "pathway", min_relevance, overlap_items=common_pathways
            )
            filtered_pathways_b = _filter_items_by_relevance(
                pathways_b, query_context, "pathway", min_relevance, overlap_items=common_pathways
            )
            
            mech["pathways_a"] = filtered_pathways_a
            mech["pathways_b"] = filtered_pathways_b
            filtered_context["signals"]["mechanistic"] = mech
            
            metadata["items_after"] += len(filtered_pathways_a) + len(filtered_pathways_b)
            if len(filtered_pathways_a) < len(pathways_a) or len(filtered_pathways_b) < len(pathways_b):
                metadata["filtered_sections"].append("pathways")
        
        # Filter FAERS data
        if "faers" in signals:
            faers = signals["faers"].copy()
            # FAERS data is already top-K truncated, so we keep it as-is
            # but could filter if needed
            filtered_context["signals"]["faers"] = faers
    
    # Preserve other context sections
    for key in ["drugs", "caveats", "pkpd", "sources", "meta"]:
        if key in context:
            filtered_context[key] = context[key]
    
    metadata["filter_ratio"] = (
        (metadata["items_before"] - metadata["items_after"]) / metadata["items_before"]
        if metadata["items_before"] > 0 else 0.0
    )
    
    return filtered_context, metadata


def _filter_items_by_relevance(
    items: List[str],
    query_context: Dict[str, Any],
    item_type: str,
    min_relevance: float,
    overlap_items: Optional[List[str]] = None
) -> List[str]:
    """
    Filter a list of items by relevance score.
    
    Args:
        items: List of item names/IDs
        query_context: Query context for scoring
        item_type: Type of items ("side_effect", "target", "pathway")
        min_relevance: Minimum relevance threshold
        overlap_items: Optional list of overlapping items (get bonus score)
        
    Returns:
        Filtered list of items
    """
    if not items:
        return []
    
    overlap_set = set(overlap_items or [])
    scored_items = []
    
    for item in items:
        item_dict = {
            "name": item,
        }
        
        # Add overlap flags
        if item_type == "target":
            item_dict["target_overlap"] = item in overlap_set
        elif item_type == "pathway":
            item_dict["pathway_overlap"] = item in overlap_set
        
        score = score_evidence_item(item_dict, query_context, item_type=item_type)
        
        if score >= min_relevance:
            scored_items.append((item, score))
    
    # Sort by score and return items
    scored_items.sort(key=lambda x: x[1], reverse=True)
    return [item for item, _ in scored_items]


def filter_context_sections(
    context: Dict[str, Any],
    query: str,
    query_context: Dict[str, Any],
    sections_to_filter: Optional[List[str]] = None,
    min_relevance: float = DEFAULT_RELEVANCE_THRESHOLD
) -> Dict[str, Any]:
    """
    Filter specific sections of context by relevance.
    
    Args:
        context: Full context dictionary
        query: Query string
        query_context: Query context
        sections_to_filter: List of section names to filter (None = all)
        min_relevance: Minimum relevance threshold
        
    Returns:
        Filtered context
    """
    if sections_to_filter is None:
        sections_to_filter = ["side_effects", "targets", "pathways"]
    
    filtered_context = context.copy()
    
    # Filter side effects
    if "side_effects" in sections_to_filter and "signals" in context:
        signals = filtered_context["signals"]
        if "tabular" in signals:
            tabular = signals["tabular"]
            se_a = tabular.get("side_effects_a", [])
            se_b = tabular.get("side_effects_b", [])
            
            filtered_se_a = _filter_items_by_relevance(se_a, query_context, "side_effect", min_relevance)
            filtered_se_b = _filter_items_by_relevance(se_b, query_context, "side_effect", min_relevance)
            
            tabular["side_effects_a"] = filtered_se_a
            tabular["side_effects_b"] = filtered_se_b
    
    # Filter targets
    if "targets" in sections_to_filter and "signals" in context:
        signals = filtered_context["signals"]
        if "mechanistic" in signals:
            mech = signals["mechanistic"]
            targets_a = mech.get("targets_a", [])
            targets_b = mech.get("targets_b", [])
            overlap_targets = mech.get("common_targets", []) or []
            
            filtered_targets_a = _filter_items_by_relevance(
                targets_a, query_context, "target", min_relevance, overlap_targets
            )
            filtered_targets_b = _filter_items_by_relevance(
                targets_b, query_context, "target", min_relevance, overlap_targets
            )
            
            mech["targets_a"] = filtered_targets_a
            mech["targets_b"] = filtered_targets_b
    
    # Filter pathways
    if "pathways" in sections_to_filter and "signals" in context:
        signals = filtered_context["signals"]
        if "mechanistic" in signals:
            mech = signals["mechanistic"]
            pathways_a = mech.get("pathways_a", [])
            pathways_b = mech.get("pathways_b", [])
            common_pathways = mech.get("common_pathways", []) or []
            
            filtered_pathways_a = _filter_items_by_relevance(
                pathways_a, query_context, "pathway", min_relevance, common_pathways
            )
            filtered_pathways_b = _filter_items_by_relevance(
                pathways_b, query_context, "pathway", min_relevance, common_pathways
            )
            
            mech["pathways_a"] = filtered_pathways_a
            mech["pathways_b"] = filtered_pathways_b
    
    return filtered_context

