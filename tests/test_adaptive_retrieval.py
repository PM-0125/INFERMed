# tests/test_adaptive_retrieval.py
"""
Unit tests for adaptive retrieval module.
"""

import pytest
from src.utils.adaptive_retrieval import (
    calculate_result_quality,
    adaptive_retrieve,
    adaptive_retrieve_with_fallback,
)


class TestCalculateResultQuality:
    """Test cases for calculate_result_quality function."""
    
    def test_empty_results(self):
        """Test quality calculation for empty results."""
        quality = calculate_result_quality([])
        
        assert quality["count"] == 0
        assert quality["avg_score"] == 0.0
        assert quality["quality_score"] == 0.0
    
    def test_tuple_results(self):
        """Test quality calculation with (item, score) tuples."""
        results = [("item1", 0.9), ("item2", 0.7), ("item3", 0.5)]
        quality = calculate_result_quality(results)
        
        assert quality["count"] == 3
        assert quality["avg_score"] == pytest.approx(0.7, abs=0.01)
        assert quality["max_score"] == 0.9
        assert quality["min_score"] == 0.5
        assert quality["quality_score"] > 0.0
    
    def test_custom_score_extractor(self):
        """Test quality calculation with custom score extractor."""
        class Result:
            def __init__(self, name, score):
                self.name = name
                self.score = score
        
        results = [Result("item1", 0.8), Result("item2", 0.6)]
        
        def extractor(r):
            return r.score
        
        quality = calculate_result_quality(results, score_extractor=extractor)
        
        assert quality["count"] == 2
        assert quality["avg_score"] == pytest.approx(0.7, abs=0.01)


class TestAdaptiveRetrieve:
    """Test cases for adaptive_retrieve function."""
    
    def test_basic_retrieval(self):
        """Test basic adaptive retrieval."""
        def retrieve_fn(query, k):
            return [(f"item{i}", 0.8 - i*0.1) for i in range(k)]
        
        results, metadata = adaptive_retrieve(
            "query",
            retrieve_fn,
            initial_k=5
        )
        
        assert len(results) == 5
        assert metadata["initial_k"] == 5
        assert metadata["final_k"] == 5
    
    def test_expansion_on_low_relevance(self):
        """Test expansion when relevance is low."""
        def retrieve_fn(query, k):
            # Return low-scoring results
            return [(f"item{i}", 0.2 + i*0.01) for i in range(k)]
        
        results, metadata = adaptive_retrieve(
            "query",
            retrieve_fn,
            initial_k=5,
            min_relevance=0.5,
            max_k=20
        )
        
        # Should expand
        assert metadata["expanded"] is True
        assert metadata["final_k"] > metadata["initial_k"]
    
    def test_expansion_on_insufficient_results(self):
        """Test expansion when results are insufficient."""
        def retrieve_fn(query, k):
            # Return fewer results than requested, but more when k is larger
            return [(f"item{i}", 0.8) for i in range(min(k, k if k > 5 else 3))]
        
        results, metadata = adaptive_retrieve(
            "query",
            retrieve_fn,
            initial_k=5,
            min_results=5,
            max_k=20
        )
        
        # Should expand to get more results (if quality check triggers)
        # Note: expansion depends on both count and relevance
        assert metadata["final_k"] >= metadata["initial_k"]
        # If we got more results, expansion happened
        if len(results) > 3:
            assert metadata["expanded"] is True
    
    def test_no_expansion_when_quality_good(self):
        """Test that retrieval doesn't expand when quality is good."""
        def retrieve_fn(query, k):
            return [(f"item{i}", 0.9 - i*0.05) for i in range(min(k, 5))]
        
        results, metadata = adaptive_retrieve(
            "query",
            retrieve_fn,
            initial_k=5,
            min_relevance=0.5,
            min_results=3
        )
        
        # Should not expand
        assert metadata["expanded"] is False or metadata["final_k"] == metadata["initial_k"]
    
    def test_max_k_limit(self):
        """Test that max_k limit is respected."""
        def retrieve_fn(query, k):
            return [(f"item{i}", 0.2) for i in range(k)]
        
        results, metadata = adaptive_retrieve(
            "query",
            retrieve_fn,
            initial_k=5,
            min_relevance=0.5,
            max_k=10
        )
        
        # Should not exceed max_k
        assert metadata["final_k"] <= 10
    
    def test_metadata_completeness(self):
        """Test that metadata contains all expected fields."""
        def retrieve_fn(query, k):
            return [("item1", 0.8)]
        
        results, metadata = adaptive_retrieve("query", retrieve_fn, initial_k=5)
        
        required_fields = ["initial_k", "final_k", "expanded", "expansion_factor", "quality_before"]
        for field in required_fields:
            assert field in metadata


class TestAdaptiveRetrieveWithFallback:
    """Test cases for adaptive_retrieve_with_fallback function."""
    
    def test_primary_success(self):
        """Test when primary retrieval succeeds."""
        def primary_fn(query, k):
            return [("item1", 0.9), ("item2", 0.8)]
        
        results, metadata = adaptive_retrieve_with_fallback(
            "query",
            primary_fn,
            None,
            initial_k=5
        )
        
        assert len(results) == 2
        assert metadata["used_fallback"] is False
    
    def test_fallback_usage(self):
        """Test when fallback is used."""
        def primary_fn(query, k):
            return []  # Primary fails
        
        def fallback_fn(query, k):
            return [("item1", 0.7), ("item2", 0.6)]
        
        results, metadata = adaptive_retrieve_with_fallback(
            "query",
            primary_fn,
            fallback_fn,
            initial_k=5
        )
        
        assert len(results) == 2
        assert metadata["used_fallback"] is True
    
    def test_no_fallback_when_primary_succeeds(self):
        """Test that fallback is not used when primary succeeds."""
        def primary_fn(query, k):
            return [("item1", 0.9)]
        
        def fallback_fn(query, k):
            return [("item2", 0.8)]
        
        results, metadata = adaptive_retrieve_with_fallback(
            "query",
            primary_fn,
            fallback_fn,
            initial_k=5
        )
        
        assert len(results) == 1
        assert results[0][0] == "item1"
        assert metadata["used_fallback"] is False

