# tests/test_relevance_scoring.py
"""
Unit tests for relevance scoring module.
"""

import pytest
from src.utils.relevance_scoring import (
    score_evidence_item,
    score_and_rank_side_effects,
    score_and_rank_pathways,
    score_and_rank_targets,
    apply_relevance_filter,
    merge_and_rerank_evidence,
)


class TestScoreEvidenceItem:
    """Test cases for score_evidence_item function."""
    
    def test_canonical_interaction_high_score(self):
        """Test that canonical interactions get high scores."""
        item = {
            "canonical_interaction": True,
        }
        query_context = {}
        score = score_evidence_item(item, query_context)
        assert score >= 10.0
    
    def test_prr_scoring(self):
        """Test PRR-based scoring."""
        # High PRR
        item_high = {"prr": 2.5}
        score_high = score_evidence_item(item_high, {})
        assert score_high >= 5.0
        
        # Moderate PRR
        item_mod = {"prr": 1.7}
        score_mod = score_evidence_item(item_mod, {})
        assert 2.0 <= score_mod < 5.0
        
        # Low PRR
        item_low = {"prr": 1.2}
        score_low = score_evidence_item(item_low, {})
        assert 0.5 <= score_low < 2.0
    
    def test_pathway_overlap_scoring(self):
        """Test pathway overlap scoring."""
        item = {"pathway_overlap": True}
        score = score_evidence_item(item, {})
        assert score >= 3.0
    
    def test_target_overlap_scoring(self):
        """Test target overlap scoring."""
        item = {"target_overlap": True}
        score = score_evidence_item(item, {})
        assert score >= 2.0
    
    def test_enzyme_interaction_scoring(self):
        """Test enzyme interaction scoring."""
        item_inhib = {"enzyme_inhibition": True}
        score_inhib = score_evidence_item(item_inhib, {})
        assert score_inhib >= 4.0
        
        item_induc = {"enzyme_induction": True}
        score_induc = score_evidence_item(item_induc, {})
        assert score_induc >= 3.0
        
        item_sub = {"shared_substrate": True}
        score_sub = score_evidence_item(item_sub, {})
        assert score_sub >= 1.5
    
    def test_risk_flag_scoring(self):
        """Test risk flag scoring."""
        item_high = {"dili_risk": "high"}
        score_high = score_evidence_item(item_high, {})
        assert score_high >= 2.0
        
        item_mod = {"dili_risk": "moderate"}
        score_mod = score_evidence_item(item_mod, {})
        assert 1.0 <= score_mod < 2.0
    
    def test_semantic_similarity_bonus(self):
        """Test semantic similarity bonus."""
        item_high = {"semantic_similarity": 0.85}
        score_high = score_evidence_item(item_high, {})
        assert score_high >= 1.0
        
        item_mod = {"semantic_similarity": 0.65}
        score_mod = score_evidence_item(item_mod, {})
        assert 0.5 <= score_mod < 1.0
    
    def test_combined_scoring(self):
        """Test that multiple factors combine correctly."""
        item = {
            "canonical_interaction": True,
            "prr": 2.5,
            "pathway_overlap": True,
            "enzyme_inhibition": True,
        }
        score = score_evidence_item(item, {})
        # Should be sum of all factors
        assert score >= 22.0  # 10 + 5 + 3 + 4 = 22
    
    def test_empty_item(self):
        """Test scoring empty item."""
        item = {}
        score = score_evidence_item(item, {})
        assert score == 0.0


class TestScoreAndRankSideEffects:
    """Test cases for score_and_rank_side_effects function."""
    
    def test_basic_ranking(self):
        """Test basic side effect ranking."""
        side_effects = ["bleeding", "bruising", "nausea"]
        query_context = {}
        prr_data = {"bleeding": 2.5, "bruising": 1.2, "nausea": 0.8}
        
        scored = score_and_rank_side_effects(side_effects, query_context, prr_data)
        
        assert len(scored) == 3
        # Bleeding should rank highest due to high PRR
        assert scored[0][0] == "bleeding"
        assert scored[0][1] > scored[1][1]
    
    def test_ranking_without_prr(self):
        """Test ranking without PRR data."""
        side_effects = ["bleeding", "bruising"]
        query_context = {}
        scored = score_and_rank_side_effects(side_effects, query_context, None)
        
        assert len(scored) == 2
        # Should still return results, just with lower scores
        assert all(score >= 0.0 for _, score in scored)
    
    def test_ranking_with_semantic_scores(self):
        """Test ranking with semantic similarity scores."""
        side_effects = ["bleeding", "bruising"]
        query_context = {}
        prr_data = {"bleeding": 2.0, "bruising": 1.5}
        semantic_scores = {"bleeding": 0.9, "bruising": 0.7}
        
        scored = score_and_rank_side_effects(
            side_effects, query_context, prr_data, semantic_scores
        )
        
        assert len(scored) == 2
        # Bleeding should rank higher
        assert scored[0][0] == "bleeding"
    
    def test_empty_list(self):
        """Test ranking empty list."""
        scored = score_and_rank_side_effects([], {}, None)
        assert scored == []


class TestScoreAndRankPathways:
    """Test cases for score_and_rank_pathways function."""
    
    def test_pathway_ranking_with_overlap(self):
        """Test pathway ranking with overlap."""
        pathways = ["pathway1", "pathway2", "pathway3"]
        query_context = {}
        overlap_pathways = ["pathway1", "pathway3"]
        
        scored = score_and_rank_pathways(pathways, query_context, overlap_pathways)
        
        assert len(scored) == 3
        # Overlapping pathways should rank higher
        overlap_scores = [score for path, score in scored if path in overlap_pathways]
        non_overlap_scores = [score for path, score in scored if path not in overlap_pathways]
        
        assert max(overlap_scores) > max(non_overlap_scores)
    
    def test_pathway_ranking_without_overlap(self):
        """Test pathway ranking without overlap."""
        pathways = ["pathway1", "pathway2"]
        query_context = {}
        scored = score_and_rank_pathways(pathways, query_context, None)
        
        assert len(scored) == 2
        assert all(score >= 0.0 for _, score in scored)


class TestScoreAndRankTargets:
    """Test cases for score_and_rank_targets function."""
    
    def test_target_ranking_with_overlap(self):
        """Test target ranking with overlap."""
        targets = ["target1", "target2", "target3"]
        query_context = {}
        overlap_targets = ["target1"]
        
        scored = score_and_rank_targets(targets, query_context, overlap_targets)
        
        assert len(scored) == 3
        # Overlapping target should rank highest
        assert scored[0][0] == "target1"
        assert scored[0][1] > scored[1][1]


class TestApplyRelevanceFilter:
    """Test cases for apply_relevance_filter function."""
    
    def test_basic_filtering(self):
        """Test basic relevance filtering."""
        items = ["item1", "item2", "item3", "item4"]
        scores = [5.0, 3.0, 1.0, 0.5]
        
        filtered = apply_relevance_filter(items, scores, min_score=2.0)
        
        assert len(filtered) == 2
        assert "item1" in filtered
        assert "item2" in filtered
        assert "item3" not in filtered
        assert "item4" not in filtered
    
    def test_filtering_with_top_k(self):
        """Test filtering with top-k."""
        items = ["item1", "item2", "item3", "item4"]
        scores = [5.0, 4.0, 3.0, 2.0]
        
        filtered = apply_relevance_filter(items, scores, min_score=0.0, top_k=2)
        
        assert len(filtered) == 2
        assert "item1" in filtered
        assert "item2" in filtered
    
    def test_filtering_combined(self):
        """Test filtering with both min_score and top_k."""
        items = ["item1", "item2", "item3", "item4"]
        scores = [5.0, 4.0, 1.0, 0.5]
        
        filtered = apply_relevance_filter(items, scores, min_score=2.0, top_k=1)
        
        assert len(filtered) == 1
        assert "item1" in filtered


class TestMergeAndRerankEvidence:
    """Test cases for merge_and_rerank_evidence function."""
    
    def test_basic_merging(self):
        """Test basic merging of keyword and semantic results."""
        keyword_results = [("item1", 5.0), ("item2", 3.0)]
        semantic_results = [("item2", 4.0), ("item3", 2.0)]
        
        merged = merge_and_rerank_evidence(
            keyword_results, semantic_results, keyword_weight=0.6, semantic_weight=0.4
        )
        
        assert len(merged) == 3
        # item2 should be first (in both, combined score: 3.0*0.6 + 4.0*0.4 = 3.4)
        # item1 should be second (only in keyword: 5.0*0.6 = 3.0)
        assert merged[0][0] == "item2"
        assert merged[1][0] == "item1"
        assert merged[2][0] == "item3"
    
    def test_weighted_combination(self):
        """Test that weights are applied correctly."""
        keyword_results = [("item1", 10.0)]
        semantic_results = [("item1", 5.0)]
        
        # Equal weights
        merged_equal = merge_and_rerank_evidence(
            keyword_results, semantic_results, keyword_weight=0.5, semantic_weight=0.5
        )
        assert merged_equal[0][1] == 7.5  # (10 * 0.5) + (5 * 0.5)
        
        # Keyword-heavy
        merged_keyword = merge_and_rerank_evidence(
            keyword_results, semantic_results, keyword_weight=0.8, semantic_weight=0.2
        )
        assert merged_keyword[0][1] > 7.5  # Should be higher
    
    def test_items_only_in_one_source(self):
        """Test merging when items appear in only one source."""
        keyword_results = [("item1", 5.0)]
        semantic_results = [("item2", 4.0)]
        
        merged = merge_and_rerank_evidence(
            keyword_results, semantic_results, keyword_weight=0.6, semantic_weight=0.4
        )
        
        assert len(merged) == 2
        # item1 should rank higher (higher weighted score)
        assert merged[0][0] == "item1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

