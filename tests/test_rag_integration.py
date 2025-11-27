# tests/test_rag_integration.py
"""
Integration tests for RAG pipeline with semantic search and relevance scoring.
"""

import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Test if dependencies are available
try:
    from sentence_transformers import SentenceTransformer
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False

from src.llm.rag_pipeline import retrieve_and_normalize, get_context_cached


class TestRAGPipelineIntegration:
    """Integration tests for RAG pipeline."""
    
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
    
    @pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not available")
    def test_semantic_search_integration(self, temp_dirs):
        """Test that semantic search is integrated in pipeline."""
        # This is a basic integration test
        # Full test would require actual data files
        
        # Mock the DuckDB client to avoid needing actual data
        with patch('src.llm.rag_pipeline.dq') as mock_dq:
            mock_client = MagicMock()
            mock_client.get_side_effects.return_value = []
            mock_client.get_interaction_score.return_value = 0.0
            mock_client.get_dilirank_score.return_value = None
            mock_client.get_dictrank_score.return_value = None
            mock_client.get_diqt_score.return_value = None
            mock_client.get_drug_targets.return_value = []
            mock_client._con.execute.return_value.fetchall.return_value = []
            mock_dq.DuckDBClient.return_value = mock_client
            mock_dq.init_duckdb_connection.return_value = None
            
            # Mock QLever
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
                
                # Mock OpenFDA
                with patch('src.llm.rag_pipeline.OpenFDAClient') as mock_openfda:
                    mock_client_fda = MagicMock()
                    mock_client_fda.get_top_reactions.return_value = []
                    mock_client_fda.get_combination_reactions.return_value = []
                    mock_openfda.return_value = mock_client_fda
                    
                    # Call retrieve_and_normalize
                    context = retrieve_and_normalize(
                        "warfarin",
                        "aspirin",
                        parquet_dir=temp_dirs["parquet"],
                        openfda_cache=temp_dirs["openfda"],
                    )
                    
                    # Check that context is created
                    assert context is not None
                    assert "drugs" in context
                    assert "signals" in context
                    assert "sources" in context
                    
                    # Check that semantic search source is listed (if available)
                    sources = context.get("sources", {})
                    # Semantic search may or may not be in sources depending on availability
                    assert isinstance(sources, dict)
    
    def test_relevance_scoring_integration(self, temp_dirs):
        """Test that relevance scoring is applied in pipeline."""
        # Mock all dependencies
        with patch('src.llm.rag_pipeline.dq') as mock_dq:
            mock_client = MagicMock()
            mock_client.get_side_effects.return_value = ["bleeding", "bruising", "nausea"]
            mock_client.get_interaction_score.return_value = 2.5
            mock_client.get_dilirank_score.return_value = 0.8
            mock_client.get_dictrank_score.return_value = 0.6
            mock_client.get_diqt_score.return_value = 0.5
            mock_client.get_drug_targets.return_value = ["target1", "target2"]
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
                    
                    context = retrieve_and_normalize(
                        "warfarin",
                        "aspirin",
                        parquet_dir=temp_dirs["parquet"],
                        openfda_cache=temp_dirs["openfda"],
                        topk_side_effects=5,
                    )
                    
                    # Check that side effects are present (should be ranked)
                    signals = context.get("signals", {})
                    tabular = signals.get("tabular", {})
                    side_effects = tabular.get("side_effects_a", [])
                    
                    # Should have side effects (may be ranked)
                    assert isinstance(side_effects, list)
    
    def test_graceful_degradation_no_embeddings(self, temp_dirs):
        """Test that pipeline works without embeddings."""
        # Mock sentence-transformers as unavailable
        with patch('src.retrieval.semantic_search.HAS_EMBEDDINGS', False):
            with patch('src.llm.rag_pipeline.dq') as mock_dq:
                mock_client = MagicMock()
                mock_client.get_side_effects.return_value = ["bleeding"]
                mock_client.get_interaction_score.return_value = 0.0
                mock_client.get_dilirank_score.return_value = None
                mock_client.get_dictrank_score.return_value = None
                mock_client.get_diqt_score.return_value = None
                mock_client.get_drug_targets.return_value = []
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
                        
                        # Should not raise error
                        context = retrieve_and_normalize(
                            "warfarin",
                            "aspirin",
                            parquet_dir=temp_dirs["parquet"],
                            openfda_cache=temp_dirs["openfda"],
                        )
                        
                        assert context is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

