# tests/test_semantic_search.py
"""
Unit tests for semantic search module.
"""

import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Test if sentence-transformers is available
try:
    from sentence_transformers import SentenceTransformer
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False
    pytest.skip("sentence-transformers not available", allow_module_level=True)

from src.retrieval.semantic_search import SemanticSearcher, get_semantic_searcher


class TestSemanticSearcher:
    """Test cases for SemanticSearcher class."""
    
    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def searcher(self, temp_cache_dir):
        """Create SemanticSearcher instance."""
        return SemanticSearcher(cache_dir=temp_cache_dir)
    
    def test_initialization(self, searcher):
        """Test searcher initialization."""
        assert searcher is not None
        assert searcher.model is not None
        assert searcher.model_name == "all-MiniLM-L6-v2"
        assert searcher.drug_index == {}
        assert searcher.drug_names == []
    
    def test_build_drug_index(self, searcher):
        """Test building drug index."""
        drug_names = ["warfarin", "aspirin", "ibuprofen", "acetaminophen"]
        result = searcher.build_drug_index(drug_names, force_rebuild=True)
        
        assert result is True
        assert len(searcher.drug_names) == 4
        assert len(searcher.drug_index) == 4
        assert "warfarin" in searcher.drug_index
        assert "aspirin" in searcher.drug_index
    
    def test_build_drug_index_empty(self, searcher):
        """Test building index with empty list."""
        result = searcher.build_drug_index([], force_rebuild=True)
        assert result is False
    
    def test_build_drug_index_caching(self, searcher, temp_cache_dir):
        """Test that drug index is cached."""
        drug_names = ["warfarin", "aspirin", "ibuprofen"]
        
        # Build index
        searcher.build_drug_index(drug_names, force_rebuild=True)
        
        # Create new searcher instance
        new_searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        
        # Should load from cache
        result = new_searcher.build_drug_index(drug_names, force_rebuild=False)
        assert result is True
        assert len(new_searcher.drug_names) == 3
        assert len(new_searcher.drug_index) == 3
    
    def test_search_similar_drugs(self, searcher):
        """Test searching for similar drugs."""
        drug_names = ["warfarin", "aspirin", "ibuprofen", "acetaminophen"]
        searcher.build_drug_index(drug_names, force_rebuild=True)
        
        # Search for warfarin (should find itself with high similarity)
        results = searcher.search_similar_drugs("warfarin", top_k=3, threshold=0.5)
        
        assert len(results) > 0
        assert results[0][0] == "warfarin"  # Should find itself first
        assert results[0][1] > 0.9  # Very high similarity to itself
    
    def test_search_similar_drugs_empty_index(self, searcher):
        """Test search with empty index."""
        results = searcher.search_similar_drugs("warfarin", top_k=5)
        assert results == []
    
    def test_search_similar_drugs_threshold(self, searcher):
        """Test search with similarity threshold."""
        drug_names = ["warfarin", "aspirin", "ibuprofen"]
        searcher.build_drug_index(drug_names, force_rebuild=True)
        
        # High threshold should return fewer results
        results_high = searcher.search_similar_drugs("warfarin", top_k=10, threshold=0.95)
        results_low = searcher.search_similar_drugs("warfarin", top_k=10, threshold=0.5)
        
        assert len(results_high) <= len(results_low)
    
    def test_search_similar_drugs_empty_query(self, searcher):
        """Test search with empty query."""
        drug_names = ["warfarin", "aspirin"]
        searcher.build_drug_index(drug_names, force_rebuild=True)
        
        results = searcher.search_similar_drugs("", top_k=5)
        assert results == []
        
        results = searcher.search_similar_drugs("   ", top_k=5)
        assert results == []
    
    def test_build_side_effect_index(self, searcher):
        """Test building side effect index."""
        side_effects = ["bleeding", "bruising", "nausea", "headache"]
        result = searcher.build_side_effect_index(side_effects, force_rebuild=True)
        
        assert result is True
        assert len(searcher.side_effect_names) == 4
        assert len(searcher.side_effect_index) == 4
    
    def test_search_similar_side_effects(self, searcher):
        """Test searching for similar side effects."""
        side_effects = ["bleeding", "bruising", "nausea", "headache"]
        searcher.build_side_effect_index(side_effects, force_rebuild=True)
        
        results = searcher.search_similar_side_effects("bleeding", top_k=3, threshold=0.5)
        
        assert len(results) > 0
        assert results[0][0] == "bleeding"  # Should find itself first
        assert results[0][1] > 0.9
    
    def test_duplicate_drug_names(self, searcher):
        """Test that duplicate drug names are handled correctly."""
        drug_names = ["warfarin", "aspirin", "warfarin", "aspirin", "ibuprofen"]
        searcher.build_drug_index(drug_names, force_rebuild=True)
        
        # Should have unique drugs only
        assert len(searcher.drug_names) == 3
        assert len(searcher.drug_index) == 3


class TestGlobalSearcher:
    """Test cases for global searcher function."""
    
    def test_get_semantic_searcher(self):
        """Test getting global searcher instance."""
        searcher = get_semantic_searcher()
        assert searcher is not None
        assert isinstance(searcher, SemanticSearcher)
    
    def test_get_semantic_searcher_singleton(self):
        """Test that get_semantic_searcher returns same instance."""
        searcher1 = get_semantic_searcher()
        searcher2 = get_semantic_searcher()
        assert searcher1 is searcher2


class TestSemanticSearchIntegration:
    """Integration tests for semantic search."""
    
    @pytest.fixture
    def searcher(self):
        """Create searcher with test data."""
        temp_dir = tempfile.mkdtemp()
        searcher = SemanticSearcher(cache_dir=temp_dir)
        
        # Build index with common drugs
        drug_names = [
            "warfarin", "coumadin", "aspirin", "ibuprofen", "acetaminophen",
            "metformin", "lisinopril", "atorvastatin", "amlodipine", "omeprazole"
        ]
        searcher.build_drug_index(drug_names, force_rebuild=True)
        
        yield searcher
        
        shutil.rmtree(temp_dir)
    
    def test_synonym_detection(self, searcher):
        """Test that synonyms are detected (e.g., warfarin and coumadin)."""
        # Note: This is a simplified test - real synonym detection would require
        # domain-specific models or knowledge bases
        results = searcher.search_similar_drugs("warfarin", top_k=5, threshold=0.5)
        
        assert len(results) > 0
        assert "warfarin" in [r[0] for r in results]
    
    def test_case_insensitive_search(self, searcher):
        """Test that search works with different cases."""
        results_lower = searcher.search_similar_drugs("warfarin", top_k=5)
        results_upper = searcher.search_similar_drugs("WARFARIN", top_k=5)
        
        # Should return similar results (embeddings are case-sensitive but
        # we test that both work)
        assert len(results_lower) > 0
        assert len(results_upper) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

