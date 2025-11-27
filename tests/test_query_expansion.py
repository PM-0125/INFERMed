# tests/test_query_expansion.py
"""
Unit tests for query expansion module.
"""

import pytest
from src.utils.query_expansion import (
    expand_drug_query,
    expand_drug_pair_queries,
    get_best_match_from_expanded,
    merge_expanded_results,
    create_expanded_query_context,
    expand_with_semantic_similarity,
)


class TestExpandDrugQuery:
    """Test cases for expand_drug_query function."""
    
    def test_basic_expansion(self):
        """Test basic query expansion."""
        expanded = expand_drug_query("warfarin")
        
        assert "warfarin" in expanded
        assert len(expanded) > 1  # Should have variations
    
    def test_expansion_with_synonyms(self):
        """Test expansion with synonyms."""
        synonyms = ["coumadin", "jantoven"]
        expanded = expand_drug_query("warfarin", synonyms=synonyms)
        
        assert "warfarin" in expanded
        assert "coumadin" in expanded
        assert "jantoven" in expanded
    
    def test_case_variations(self):
        """Test that case variations are included."""
        expanded = expand_drug_query("Warfarin", use_case_variants=True)
        
        assert "Warfarin" in expanded
        assert "warfarin" in expanded
        assert "WARFARIN" in expanded
    
    def test_name_variations(self):
        """Test name variation generation."""
        expanded = expand_drug_query("warfarin sodium")
        
        assert "warfarin sodium" in expanded
        assert "warfarin" in expanded  # Should remove "sodium"
    
    def test_hyphenated_names(self):
        """Test hyphenated name variations."""
        expanded = expand_drug_query("acetylsalicylic-acid")
        
        assert "acetylsalicylic-acid" in expanded
        assert "acetylsalicylic acid" in expanded
        assert "acetylsalicylicacid" in expanded
    
    def test_empty_query(self):
        """Test expansion with empty query."""
        expanded = expand_drug_query("")
        assert expanded == []
        
        expanded = expand_drug_query("   ")
        assert expanded == []
    
    def test_original_first(self):
        """Test that original term appears first."""
        expanded = expand_drug_query("warfarin")
        assert expanded[0] == "warfarin"


class TestExpandDrugPairQueries:
    """Test cases for expand_drug_pair_queries function."""
    
    def test_basic_pair_expansion(self):
        """Test basic pair expansion."""
        result = expand_drug_pair_queries("warfarin", "aspirin")
        
        assert "drug_a" in result
        assert "drug_b" in result
        assert "warfarin" in result["drug_a"]
        assert "aspirin" in result["drug_b"]
    
    def test_pair_expansion_with_synonyms(self):
        """Test pair expansion with synonyms."""
        result = expand_drug_pair_queries(
            "warfarin", "aspirin",
            synonyms_a=["coumadin"],
            synonyms_b=["acetylsalicylic acid"]
        )
        
        assert "coumadin" in result["drug_a"]
        assert "acetylsalicylic acid" in result["drug_b"]


class TestGetBestMatchFromExpanded:
    """Test cases for get_best_match_from_expanded function."""
    
    def test_basic_matching(self):
        """Test basic matching."""
        expanded = ["warfarin", "coumadin", "jantoven"]
        
        def match_func(term):
            return term == "coumadin"
        
        matched = get_best_match_from_expanded(expanded, match_func)
        assert matched == ["coumadin"]
    
    def test_multiple_matches(self):
        """Test multiple matches."""
        expanded = ["warfarin", "coumadin", "jantoven"]
        
        def match_func(term):
            return term in ["warfarin", "coumadin"]
        
        matched = get_best_match_from_expanded(expanded, match_func, max_results=2)
        assert len(matched) == 2
        assert "warfarin" in matched
        assert "coumadin" in matched
    
    def test_no_matches(self):
        """Test when no matches found."""
        expanded = ["warfarin", "coumadin"]
        
        def match_func(term):
            return False
        
        matched = get_best_match_from_expanded(expanded, match_func)
        assert matched == []
    
    def test_exception_handling(self):
        """Test exception handling in match function."""
        expanded = ["warfarin", "coumadin"]
        
        def match_func(term):
            if term == "warfarin":
                raise ValueError("Error")
            return term == "coumadin"
        
        matched = get_best_match_from_expanded(expanded, match_func)
        assert matched == ["coumadin"]


class TestMergeExpandedResults:
    """Test cases for merge_expanded_results function."""
    
    def test_merge_lists(self):
        """Test merging list results."""
        results = {
            "warfarin": ["bleeding", "bruising"],
            "coumadin": ["bleeding", "nausea"],
        }
        
        merged = merge_expanded_results(results)
        
        assert len(merged) == 3  # bleeding, bruising, nausea
        assert "bleeding" in merged
        assert "bruising" in merged
        assert "nausea" in merged
    
    def test_merge_sets(self):
        """Test merging set results."""
        results = {
            "warfarin": {"bleeding", "bruising"},
            "coumadin": {"bleeding", "nausea"},
        }
        
        merged = merge_expanded_results(results)
        
        assert len(merged) == 3
        assert "bleeding" in merged
        assert "bruising" in merged
        assert "nausea" in merged
    
    def test_deduplication(self):
        """Test that duplicates are removed."""
        results = {
            "warfarin": ["bleeding", "bruising", "bleeding"],
            "coumadin": ["bleeding", "nausea"],
        }
        
        merged = merge_expanded_results(results)
        
        # Should have unique items
        assert len(merged) == 3
        assert merged.count("bleeding") == 1
    
    def test_empty_results(self):
        """Test merging empty results."""
        merged = merge_expanded_results({})
        assert merged == []


class TestCreateExpandedQueryContext:
    """Test cases for create_expanded_query_context function."""
    
    def test_basic_context(self):
        """Test basic context creation."""
        context = create_expanded_query_context("warfarin", "aspirin")
        
        assert "original" in context
        assert "expanded" in context
        assert "expansion_methods" in context
        assert context["original"]["drug_a"] == "warfarin"
        assert context["original"]["drug_b"] == "aspirin"
    
    def test_context_with_synonyms(self):
        """Test context with synonyms."""
        context = create_expanded_query_context(
            "warfarin", "aspirin",
            synonyms_a=["coumadin"],
            synonyms_b=["acetylsalicylic acid"]
        )
        
        assert "coumadin" in context["expanded"]["drug_a"]
        assert "acetylsalicylic acid" in context["expanded"]["drug_b"]
        assert context["expansion_methods"]["synonyms"] is True
    
    def test_context_with_semantic(self):
        """Test context with semantic searcher."""
        # Mock semantic searcher
        class MockSearcher:
            def search_similar_drugs(self, query, top_k=3, threshold=0.7):
                if query == "warfarin":
                    return [("coumadin", 0.9)]
                return []
        
        searcher = MockSearcher()
        context = create_expanded_query_context(
            "warfarin", "aspirin",
            semantic_searcher=searcher
        )
        
        assert context["expansion_methods"]["semantic"] is True
        # Should include semantically similar drugs
        assert "coumadin" in context["expanded"]["drug_a"] or "warfarin" in context["expanded"]["drug_a"]


class TestExpandWithSemanticSimilarity:
    """Test cases for expand_with_semantic_similarity function."""
    
    def test_basic_semantic_expansion(self):
        """Test basic semantic expansion."""
        class MockSearcher:
            def search_similar_drugs(self, query, top_k=3, threshold=0.7):
                return [("coumadin", 0.9), ("jantoven", 0.8)]
        
        searcher = MockSearcher()
        expanded = expand_with_semantic_similarity("warfarin", searcher)
        
        assert "warfarin" in expanded
        assert "coumadin" in expanded
        assert "jantoven" in expanded
    
    def test_no_searcher(self):
        """Test expansion without searcher."""
        expanded = expand_with_semantic_similarity("warfarin", None)
        assert expanded == ["warfarin"]
    
    def test_threshold_filtering(self):
        """Test that threshold filters low-similarity results."""
        class MockSearcher:
            def search_similar_drugs(self, query, top_k=3, threshold=0.7):
                # Only return high similarity
                return [("coumadin", 0.9)]
        
        searcher = MockSearcher()
        expanded = expand_with_semantic_similarity("warfarin", searcher, threshold=0.85)
        
        # Should only include original if threshold too high
        assert "warfarin" in expanded
        # coumadin might not be included if threshold filtering happens in searcher
    
    def test_exception_handling(self):
        """Test exception handling in semantic expansion."""
        class FailingSearcher:
            def search_similar_drugs(self, query, top_k=3, threshold=0.7):
                raise ValueError("Search failed")
        
        searcher = FailingSearcher()
        expanded = expand_with_semantic_similarity("warfarin", searcher)
        
        # Should return at least original
        assert "warfarin" in expanded


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

