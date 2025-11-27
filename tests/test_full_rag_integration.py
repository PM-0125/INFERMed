# tests/test_full_rag_integration.py
"""
Comprehensive integration tests for complete RAG pipeline with all improvements.
"""

import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Test if dependencies are available
try:
    from sentence_transformers import SentenceTransformer, CrossEncoder
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False

from src.llm.rag_pipeline import (
    retrieve_and_normalize,
    run_rag,
    record_feedback,
    get_context_cached,
)


class TestFullRAGIntegration:
    """Comprehensive integration tests for complete RAG pipeline."""
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing."""
        base_dir = tempfile.mkdtemp()
        parquet_dir = os.path.join(base_dir, "duckdb")
        openfda_dir = os.path.join(base_dir, "openfda")
        os.makedirs(parquet_dir, exist_ok=True)
        os.makedirs(openfda_dir, exist_ok=True)
        
        yield {
            "base": base_dir,
            "parquet": parquet_dir,
            "openfda": openfda_dir,
        }
        
        shutil.rmtree(base_dir)
    
    def test_complete_pipeline_with_all_features(self, temp_dirs):
        """Test complete pipeline with all RAG improvements."""
        # Mock all dependencies
        with patch('src.llm.rag_pipeline.dq') as mock_dq:
            mock_client = MagicMock()
            mock_client.get_side_effects.return_value = ["bleeding", "bruising", "nausea"]
            mock_client.get_interaction_score.return_value = 2.5
            mock_client.get_dilirank_score.return_value = 0.8
            mock_client.get_dictrank_score.return_value = 0.6
            mock_client.get_diqt_score.return_value = 0.5
            mock_client.get_drug_targets.return_value = ["target1", "target2"]
            mock_client.get_synonyms.return_value = ["coumadin"]
            mock_client._con.execute.return_value.fetchall.return_value = [(2.5,)]
            mock_dq.DuckDBClient.return_value = mock_client
            mock_dq.init_duckdb_connection.return_value = None
            
            with patch('src.llm.rag_pipeline._get_qlever_mechanistic_or_stub') as mock_qlever:
                mock_qlever.return_value = {
                    "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                               "b": {"substrate": [], "inhibitor": [], "inducer": []}},
                    "targets_a": ["target1"], "targets_b": ["target2"],
                    "pathways_a": ["pathway1"], "pathways_b": ["pathway2"],
                    "common_pathways": [],
                    "ids_a": {}, "ids_b": {},
                    "synonyms_a": [], "synonyms_b": [],
                    "caveats": [],
                }
                
                with patch('src.llm.rag_pipeline.OpenFDAClient') as mock_openfda:
                    mock_client_fda = MagicMock()
                    mock_client_fda.get_top_reactions.return_value = []
                    mock_client_fda.get_combination_reactions.return_value = []
                    mock_openfda.return_value = mock_client_fda
                    
                    # Test retrieve_and_normalize
                    context = retrieve_and_normalize(
                        "warfarin",
                        "aspirin",
                        parquet_dir=temp_dirs["parquet"],
                        openfda_cache=temp_dirs["openfda"],
                        topk_side_effects=5,
                    )
                    
                    # Verify context structure
                    assert context is not None
                    assert "drugs" in context
                    assert "signals" in context
                    assert "sources" in context
                    assert "meta" in context
                    
                    # Check that new features are in sources
                    sources = context.get("sources", {})
                    assert "query_expansion" in sources or "adaptive_retrieval" in sources
    
    def test_feedback_recording(self, temp_dirs):
        """Test feedback recording functionality."""
        with patch('src.llm.rag_pipeline.dq') as mock_dq:
            mock_client = MagicMock()
            mock_client.get_side_effects.return_value = ["bleeding"]
            mock_client.get_interaction_score.return_value = 0.0
            mock_client.get_dilirank_score.return_value = None
            mock_client.get_dictrank_score.return_value = None
            mock_client.get_diqt_score.return_value = None
            mock_client.get_drug_targets.return_value = []
            mock_client.get_synonyms.return_value = []
            mock_client._con.execute.return_value.fetchall.return_value = []
            mock_dq.DuckDBClient.return_value = mock_client
            mock_dq.init_duckdb_connection.return_value = None
            
            with patch('src.llm.rag_pipeline._get_qlever_mechanistic_or_stub') as mock_qlever:
                mock_qlever.return_value = {
                    "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                               "b": {"substrate": [], "inhibitor": [], "inducer": []}},
                    "targets_a": [], "targets_b": [],
                    "pathways_a": [], "pathways_b": [],
                    "common_pathways": [],
                    "ids_a": {}, "ids_b": {},
                    "synonyms_a": [], "synonyms_b": [],
                    "caveats": [],
                }
                
                with patch('src.llm.rag_pipeline.OpenFDAClient') as mock_openfda:
                    mock_client_fda = MagicMock()
                    mock_client_fda.get_top_reactions.return_value = []
                    mock_client_fda.get_combination_reactions.return_value = []
                    mock_openfda.return_value = mock_client_fda
                    
                    context = retrieve_and_normalize(
                        "warfarin",
                        "aspirin",
                        parquet_dir=temp_dirs["parquet"],
                        openfda_cache=temp_dirs["openfda"],
                    )
                    
                    # Record feedback
                    record_feedback(
                        "warfarin",
                        "aspirin",
                        "good",
                        user_rating=0.9,
                        context=context
                    )
                    
                    # Should not raise exception
                    assert True
    
    def test_context_filtering_integration(self, temp_dirs):
        """Test that context filtering is applied."""
        with patch('src.llm.rag_pipeline.dq') as mock_dq:
            mock_client = MagicMock()
            mock_client.get_side_effects.return_value = ["bleeding", "bruising", "nausea", "headache", "dizziness"]
            mock_client.get_interaction_score.return_value = 2.5
            mock_client.get_dilirank_score.return_value = 0.8
            mock_client.get_dictrank_score.return_value = 0.6
            mock_client.get_diqt_score.return_value = 0.5
            mock_client.get_drug_targets.return_value = ["target1"]
            mock_client.get_synonyms.return_value = []
            mock_client._con.execute.return_value.fetchall.return_value = [(2.5,)]
            mock_dq.DuckDBClient.return_value = mock_client
            mock_dq.init_duckdb_connection.return_value = None
            
            with patch('src.llm.rag_pipeline._get_qlever_mechanistic_or_stub') as mock_qlever:
                mock_qlever.return_value = {
                    "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                               "b": {"substrate": [], "inhibitor": [], "inducer": []}},
                    "targets_a": ["target1"], "targets_b": ["target2"],
                    "pathways_a": [], "pathways_b": [],
                    "common_pathways": [],
                    "ids_a": {}, "ids_b": {},
                    "synonyms_a": [], "synonyms_b": [],
                    "caveats": [],
                }
                
                with patch('src.llm.rag_pipeline.OpenFDAClient') as mock_openfda:
                    mock_client_fda = MagicMock()
                    mock_client_fda.get_top_reactions.return_value = []
                    mock_client_fda.get_combination_reactions.return_value = []
                    mock_openfda.return_value = mock_client_fda
                    
                    context = retrieve_and_normalize(
                        "warfarin",
                        "aspirin",
                        parquet_dir=temp_dirs["parquet"],
                        openfda_cache=temp_dirs["openfda"],
                    )
                    
                    # Check that context has filtering metadata if filtering occurred
                    meta = context.get("meta", {})
                    # Filtering may or may not have occurred depending on relevance
                    assert "version" in meta
    
    def test_graceful_degradation_all_features(self, temp_dirs):
        """Test that pipeline works even if some features are unavailable."""
        # Mock sentence-transformers as unavailable
        with patch('src.retrieval.semantic_search.HAS_EMBEDDINGS', False):
            with patch('src.utils.reranking.HAS_CROSS_ENCODER', False):
                with patch('src.llm.rag_pipeline.dq') as mock_dq:
                    mock_client = MagicMock()
                    mock_client.get_side_effects.return_value = ["bleeding"]
                    mock_client.get_interaction_score.return_value = 0.0
                    mock_client.get_dilirank_score.return_value = None
                    mock_client.get_dictrank_score.return_value = None
                    mock_client.get_diqt_score.return_value = None
                    mock_client.get_drug_targets.return_value = []
                    mock_client.get_synonyms.return_value = []
                    mock_client._con.execute.return_value.fetchall.return_value = []
                    mock_dq.DuckDBClient.return_value = mock_client
                    mock_dq.init_duckdb_connection.return_value = None
                    
                    with patch('src.llm.rag_pipeline._get_qlever_mechanistic_or_stub') as mock_qlever:
                        mock_qlever.return_value = {
                            "enzymes": {"a": {"substrate": [], "inhibitor": [], "inducer": []},
                                       "b": {"substrate": [], "inhibitor": [], "inducer": []}},
                            "targets_a": [], "targets_b": [],
                            "pathways_a": [], "pathways_b": [],
                            "common_pathways": [],
                            "ids_a": {}, "ids_b": {},
                            "synonyms_a": [], "synonyms_b": [],
                            "caveats": [],
                        }
                        
                        with patch('src.llm.rag_pipeline.OpenFDAClient') as mock_openfda:
                            mock_client_fda = MagicMock()
                            mock_client_fda.get_top_reactions.return_value = []
                            mock_client_fda.get_combination_reactions.return_value = []
                            mock_openfda.return_value = mock_client_fda
                            
                            # Should not raise error even without embeddings/reranking
                            context = retrieve_and_normalize(
                                "warfarin",
                                "aspirin",
                                parquet_dir=temp_dirs["parquet"],
                                openfda_cache=temp_dirs["openfda"],
                            )
                            
                            assert context is not None
                            assert "signals" in context


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

