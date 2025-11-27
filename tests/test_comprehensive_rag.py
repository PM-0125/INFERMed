# tests/test_comprehensive_rag.py
"""
Comprehensive end-to-end test for complete RAG pipeline with all features.
"""

import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock

from src.llm.rag_pipeline import run_rag, record_feedback


class TestComprehensiveRAG:
    """Comprehensive end-to-end tests."""
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
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
    
    def test_complete_pipeline_warfarin_aspirin(self, temp_dirs):
        """Test complete pipeline with a known drug pair."""
        with patch('src.llm.rag_pipeline.dq') as mock_dq:
            mock_client = MagicMock()
            mock_client.get_side_effects.return_value = ["bleeding", "bruising", "gastrointestinal hemorrhage"]
            mock_client.get_interaction_score.return_value = 2.8
            mock_client.get_dilirank_score.return_value = 0.7
            mock_client.get_dictrank_score.return_value = 0.5
            mock_client.get_diqt_score.return_value = 0.3
            mock_client.get_drug_targets.return_value = ["COX1", "COX2"]
            mock_client.get_synonyms.return_value = ["coumadin"]
            mock_client._con.execute.return_value.fetchall.return_value = [(2.8,), (1.5,)]
            mock_dq.DuckDBClient.return_value = mock_client
            mock_dq.init_duckdb_connection.return_value = None
            
            with patch('src.llm.rag_pipeline._get_qlever_mechanistic_or_stub') as mock_qlever:
                mock_qlever.return_value = {
                    "enzymes": {
                        "a": {"substrate": ["CYP2C9"], "inhibitor": [], "inducer": []},
                        "b": {"substrate": [], "inhibitor": ["CYP2C9"], "inducer": []}
                    },
                    "targets_a": ["VKORC1"], "targets_b": ["COX1", "COX2"],
                    "pathways_a": ["Blood coagulation"], "pathways_b": ["Arachidonic acid metabolism"],
                    "common_pathways": [],
                    "ids_a": {"pubchem": "54678486"}, "ids_b": {"pubchem": "2244"},
                    "synonyms_a": ["coumadin"], "synonyms_b": ["acetylsalicylic acid"],
                    "caveats": [],
                }
                
                with patch('src.llm.rag_pipeline.OpenFDAClient') as mock_openfda:
                    mock_client_fda = MagicMock()
                    mock_client_fda.get_top_reactions.return_value = [("bleeding", 150), ("bruising", 80)]
                    mock_client_fda.get_combination_reactions.return_value = [("gastrointestinal hemorrhage", 45)]
                    mock_openfda.return_value = mock_client_fda
                    
                    # Run complete pipeline
                    result = run_rag(
                        "warfarin",
                        "aspirin",
                        mode="Doctor",
                        parquet_dir=temp_dirs["parquet"],
                        openfda_cache=temp_dirs["openfda"],
                        use_cache_context=False,
                        use_cache_response=False,
                    )
                    
                    # Verify result structure
                    assert "context" in result
                    assert "answer" in result
                    
                    context = result["context"]
                    assert "drugs" in context
                    assert "signals" in context
                    assert "pkpd" in context
                    assert "sources" in context
                    
                    # Check that new features are present
                    sources = context.get("sources", {})
                    assert isinstance(sources, dict)
                    
                    # Verify answer structure
                    answer = result["answer"]
                    assert "text" in answer or "response" in answer or "output" in answer
    
    def test_feedback_integration(self, temp_dirs):
        """Test feedback recording integration."""
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
                    
                    result = run_rag(
                        "warfarin",
                        "aspirin",
                        parquet_dir=temp_dirs["parquet"],
                        openfda_cache=temp_dirs["openfda"],
                        use_cache_context=False,
                    )
                    
                    # Record feedback
                    record_feedback(
                        "warfarin",
                        "aspirin",
                        "good",
                        user_rating=0.9,
                        context=result["context"]
                    )
                    
                    # Should complete without error
                    assert True

