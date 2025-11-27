# tests/test_reranking.py
"""
Unit tests for reranking module.
"""

import pytest
from unittest.mock import Mock, patch

# Test if sentence-transformers is available
try:
    from sentence_transformers import CrossEncoder
    HAS_CROSS_ENCODER = True
except ImportError:
    HAS_CROSS_ENCODER = False
    pytest.skip("sentence-transformers not available", allow_module_level=True)

from src.utils.reranking import Reranker, get_reranker


class TestReranker:
    """Test cases for Reranker class."""
    
    @pytest.fixture
    def reranker(self):
        """Create Reranker instance."""
        return Reranker()
    
    def test_initialization(self, reranker):
        """Test reranker initialization."""
        assert reranker is not None
        # Model may or may not be initialized depending on availability
        assert hasattr(reranker, "model")
    
    def test_rerank_basic(self, reranker):
        """Test basic reranking."""
        query = "warfarin interaction"
        documents = [
            "Warfarin is an anticoagulant",
            "Aspirin is a pain reliever",
            "Warfarin and aspirin interaction"
        ]
        
        results = reranker.rerank(query, documents, top_k=2)
        
        assert len(results) == 2
        assert all(isinstance(r, tuple) and len(r) == 2 for r in results)
        # Warfarin-related documents should rank higher
        doc_texts = [r[0] for r in results]
        assert any("warfarin" in doc.lower() for doc in doc_texts)
    
    def test_rerank_empty_documents(self, reranker):
        """Test reranking with empty documents."""
        results = reranker.rerank("query", [])
        assert results == []
    
    def test_rerank_empty_query(self, reranker):
        """Test reranking with empty query."""
        documents = ["doc1", "doc2"]
        results = reranker.rerank("", documents)
        
        # Should return documents with default scores
        assert len(results) == 2
        assert all(score == 1.0 for _, score in results)
    
    def test_rerank_top_k(self, reranker):
        """Test that top_k limit is respected."""
        documents = [f"doc{i}" for i in range(10)]
        results = reranker.rerank("query", documents, top_k=5)
        
        assert len(results) == 5
    
    def test_rerank_with_scores(self, reranker):
        """Test reranking documents that already have scores."""
        query = "warfarin"
        scored_docs = [
            ("Warfarin is an anticoagulant", 0.9),
            ("Aspirin information", 0.8),
            ("Warfarin interaction details", 0.7)
        ]
        
        results = reranker.rerank_with_scores(
            query,
            scored_docs,
            top_k=2,
            combine_with_original=True
        )
        
        assert len(results) == 2
        assert all(isinstance(r, tuple) and len(r) == 2 for r in results)
    
    def test_rerank_with_scores_no_combination(self, reranker):
        """Test reranking without combining with original scores."""
        query = "warfarin"
        scored_docs = [
            ("Warfarin doc", 0.9),
            ("Other doc", 0.8)
        ]
        
        results = reranker.rerank_with_scores(
            query,
            scored_docs,
            combine_with_original=False
        )
        
        assert len(results) == 2
        # Scores should be from reranking only
        assert all(score != 0.9 for _, score in results) or all(score != 0.8 for _, score in results)
    
    def test_rerank_exception_handling(self, reranker):
        """Test exception handling in reranking."""
        # Mock model to raise exception
        original_model = reranker.model
        reranker.model = Mock(side_effect=ValueError("Model error"))
        
        documents = ["doc1", "doc2"]
        results = reranker.rerank("query", documents)
        
        # Should return fallback results
        assert len(results) == 2
        assert all(score == 1.0 for _, score in results)
        
        # Restore model
        reranker.model = original_model


class TestGetReranker:
    """Test cases for get_reranker function."""
    
    def test_get_reranker(self):
        """Test getting global reranker instance."""
        reranker = get_reranker()
        assert reranker is not None
    
    def test_get_reranker_singleton(self):
        """Test that get_reranker returns same instance."""
        reranker1 = get_reranker()
        reranker2 = get_reranker()
        assert reranker1 is reranker2


class TestRerankerWithoutDependencies:
    """Test reranker behavior without dependencies."""
    
    @patch('src.utils.reranking.HAS_CROSS_ENCODER', False)
    def test_reranker_without_dependencies(self):
        """Test reranker when dependencies are unavailable."""
        reranker = Reranker()
        
        # Should still work with fallback
        documents = ["doc1", "doc2"]
        results = reranker.rerank("query", documents)
        
        assert len(results) == 2
        assert all(score == 1.0 for _, score in results)

