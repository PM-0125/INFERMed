# tests/test_hybrid_search.py
"""
Unit tests for hybrid search module.
"""

import pytest
from src.retrieval.hybrid_search import (
    hybrid_search_drugs,
    hybrid_search_side_effects,
    adaptive_hybrid_search,
)


class TestHybridSearchDrugs:
    """Test cases for hybrid_search_drugs function."""
    
    def test_keyword_only(self):
        """Test hybrid search with keyword only."""
        def keyword_fn(query, k):
            return [("warfarin", 1.0), ("aspirin", 0.8)]
        
        results = hybrid_search_drugs("warfarin", keyword_fn, None, top_k=5)
        
        assert len(results) == 2
        assert results[0][0] == "warfarin"
    
    def test_keyword_and_semantic(self):
        """Test hybrid search with both keyword and semantic."""
        def keyword_fn(query, k):
            return [("warfarin", 1.0), ("aspirin", 0.8)]
        
        def semantic_fn(query, k, threshold):
            return [("coumadin", 0.9), ("warfarin", 0.95)]
        
        results = hybrid_search_drugs(
            "warfarin",
            keyword_fn,
            semantic_fn,
            top_k=5,
            keyword_weight=0.6,
            semantic_weight=0.4
        )
        
        assert len(results) >= 2
        # Should include both keyword and semantic results
        drug_names = [r[0] for r in results]
        assert "warfarin" in drug_names
        assert "coumadin" in drug_names
    
    def test_semantic_failure_graceful(self):
        """Test graceful handling of semantic search failure."""
        def keyword_fn(query, k):
            return [("warfarin", 1.0)]
        
        def semantic_fn(query, k, threshold):
            raise ValueError("Semantic search failed")
        
        results = hybrid_search_drugs("warfarin", keyword_fn, semantic_fn, top_k=5)
        
        # Should still return keyword results
        assert len(results) == 1
        assert results[0][0] == "warfarin"
    
    def test_top_k_limit(self):
        """Test that top_k limit is respected."""
        def keyword_fn(query, k):
            return [(f"drug{i}", 1.0 - i*0.1) for i in range(20)]
        
        results = hybrid_search_drugs("query", keyword_fn, None, top_k=5)
        
        assert len(results) == 5


class TestHybridSearchSideEffects:
    """Test cases for hybrid_search_side_effects function."""
    
    def test_keyword_only(self):
        """Test side effect hybrid search with keyword only."""
        def keyword_fn(query, k):
            return ["bleeding", "bruising"]
        
        results = hybrid_search_side_effects("bleeding", keyword_fn, None, top_k=5)
        
        assert len(results) == 2
        assert results[0][0] == "bleeding"
    
    def test_keyword_and_semantic(self):
        """Test side effect hybrid search with both methods."""
        def keyword_fn(query, k):
            return ["bleeding", "bruising"]
        
        def semantic_fn(query, k, threshold):
            return [("hemorrhage", 0.9), ("bleeding", 0.95)]
        
        results = hybrid_search_side_effects(
            "bleeding",
            keyword_fn,
            semantic_fn,
            top_k=5
        )
        
        assert len(results) >= 2
        effect_names = [r[0] for r in results]
        assert "bleeding" in effect_names
        assert "hemorrhage" in effect_names or "bruising" in effect_names


class TestAdaptiveHybridSearch:
    """Test cases for adaptive_hybrid_search function."""
    
    def test_basic_adaptive_search(self):
        """Test basic adaptive hybrid search."""
        def keyword_fn(query, k):
            return [("warfarin", 1.0), ("aspirin", 0.8)]
        
        results, metadata = adaptive_hybrid_search(
            "warfarin",
            keyword_fn,
            None,
            initial_k=5
        )
        
        assert len(results) >= 1
        assert "initial_k" in metadata
        assert "final_k" in metadata
        assert metadata["initial_k"] == 5
    
    def test_expansion_on_low_relevance(self):
        """Test that search expands when relevance is low."""
        def keyword_fn(query, k):
            # Return low-scoring results
            return [(f"drug{i}", 0.1 + i*0.01) for i in range(k)]
        
        results, metadata = adaptive_hybrid_search(
            "warfarin",
            keyword_fn,
            None,
            initial_k=5,
            min_relevance_threshold=0.5,
            max_k=20
        )
        
        # Should expand if relevance is low
        assert metadata["final_k"] >= metadata["initial_k"]
    
    def test_no_expansion_when_quality_good(self):
        """Test that search doesn't expand when quality is good."""
        def keyword_fn(query, k):
            # Return high-scoring results
            return [(f"drug{i}", 0.9 - i*0.05) for i in range(min(k, 5))]
        
        results, metadata = adaptive_hybrid_search(
            "warfarin",
            keyword_fn,
            None,
            initial_k=5,
            min_relevance_threshold=0.5,
            max_k=20
        )
        
        # Should not expand if quality is good
        assert metadata["final_k"] == metadata["initial_k"] or not metadata["expanded"]
    
    def test_metadata_completeness(self):
        """Test that metadata contains all expected fields."""
        def keyword_fn(query, k):
            return [("warfarin", 1.0)]
        
        results, metadata = adaptive_hybrid_search(
            "warfarin",
            keyword_fn,
            None,
            initial_k=5
        )
        
        required_fields = ["initial_k", "final_k", "expanded", "keyword_results_count"]
        for field in required_fields:
            assert field in metadata

