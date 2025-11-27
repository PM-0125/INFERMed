# src/utils/reranking.py
# -*- coding: utf-8 -*-
"""
Re-ranking module using cross-encoders for improved relevance ordering.

This module provides re-ranking functionality to improve the order
of retrieved results based on query-document relevance.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

try:
    from sentence_transformers import CrossEncoder
    HAS_CROSS_ENCODER = True
except ImportError:
    HAS_CROSS_ENCODER = False
    CrossEncoder = None

LOG = logging.getLogger(__name__)

# Default model
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class Reranker:
    """
    Re-ranker using cross-encoder models for query-document relevance.
    """
    
    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL):
        self.model_name = model_name
        self.model = None
        self._initialized = False
        
        if HAS_CROSS_ENCODER:
            self._initialize_model()
        else:
            LOG.warning("sentence-transformers not available. Re-ranking will be disabled.")
    
    def _initialize_model(self):
        """Initialize the cross-encoder model."""
        if self.model is not None:
            return
        
        try:
            self.model = CrossEncoder(self.model_name)
            self._initialized = True
            LOG.info(f"Initialized re-ranker model: {self.model_name}")
        except Exception as e:
            LOG.error(f"Failed to initialize re-ranker model: {e}")
            self.model = None
            self._initialized = False
    
    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: Optional[int] = None
    ) -> List[Tuple[str, float]]:
        """
        Re-rank documents by relevance to query.
        
        Args:
            query: Query string
            documents: List of document strings to re-rank
            top_k: Optional number of top results to return
            
        Returns:
            List of (document, relevance_score) tuples, sorted by score (descending)
        """
        if not self._initialized or self.model is None or not documents:
            # Fallback: return documents with default scores
            if top_k is not None:
                return [(doc, 1.0) for doc in documents[:top_k]]
            return [(doc, 1.0) for doc in documents]
        
        if not query or not query.strip():
            if top_k is not None:
                return [(doc, 1.0) for doc in documents[:top_k]]
            return [(doc, 1.0) for doc in documents]
        
        try:
            # Create query-document pairs
            pairs = [(query, doc) for doc in documents]
            
            # Get relevance scores
            scores = self.model.predict(pairs)
            
            # Combine documents with scores
            scored_docs = list(zip(documents, scores))
            
            # Sort by score (descending)
            scored_docs.sort(key=lambda x: x[1], reverse=True)
            
            # Apply top-k if specified
            if top_k is not None:
                scored_docs = scored_docs[:top_k]
            
            return scored_docs
            
        except Exception as e:
            LOG.error(f"Re-ranking failed: {e}")
            # Fallback: return original order with default scores
            if top_k is not None:
                return [(doc, 1.0) for doc in documents[:top_k]]
            return [(doc, 1.0) for doc in documents]
    
    def rerank_with_scores(
        self,
        query: str,
        scored_documents: List[Tuple[str, float]],
        top_k: Optional[int] = None,
        combine_with_original: bool = True,
        original_weight: float = 0.3,
        rerank_weight: float = 0.7
    ) -> List[Tuple[str, float]]:
        """
        Re-rank documents that already have scores, optionally combining with original scores.
        
        Args:
            query: Query string
            scored_documents: List of (document, original_score) tuples
            top_k: Optional number of results to return
            combine_with_original: If True, combine rerank scores with original scores
            original_weight: Weight for original scores (if combining)
            rerank_weight: Weight for rerank scores (if combining)
            
        Returns:
            List of (document, combined_score) tuples, sorted by score
        """
        if not scored_documents:
            return []
        
        documents = [doc for doc, _ in scored_documents]
        original_scores = {doc: score for doc, score in scored_documents}
        
        # Re-rank
        reranked = self.rerank(query, documents, top_k=None)
        
        # Combine scores if requested
        if combine_with_original:
            combined = []
            for doc, rerank_score in reranked:
                original_score = original_scores.get(doc, 0.0)
                # Normalize scores to 0-1 range for combination
                # Assuming rerank scores are already in reasonable range
                combined_score = (original_score * original_weight) + (float(rerank_score) * rerank_weight)
                combined.append((doc, combined_score))
            
            # Re-sort by combined score
            combined.sort(key=lambda x: x[1], reverse=True)
            
            if top_k is not None:
                combined = combined[:top_k]
            
            return combined
        else:
            # Just use rerank scores
            if top_k is not None:
                reranked = reranked[:top_k]
            return reranked


# Global reranker instance
_global_reranker: Optional[Reranker] = None


def get_reranker(model_name: str = DEFAULT_RERANKER_MODEL) -> Optional[Reranker]:
    """Get or create global reranker instance."""
    global _global_reranker
    if _global_reranker is None and HAS_CROSS_ENCODER:
        _global_reranker = Reranker(model_name)
    return _global_reranker

