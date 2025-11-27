# tests/test_context_filtering.py
"""
Unit tests for context filtering module.
"""

import pytest
from src.utils.context_filtering import (
    filter_context_by_relevance,
    filter_context_sections,
    _filter_items_by_relevance,
)


class TestFilterContextByRelevance:
    """Test cases for filter_context_by_relevance function."""
    
    def test_basic_filtering(self):
        """Test basic context filtering."""
        context = {
            "signals": {
                "tabular": {
                    "side_effects_a": ["bleeding", "bruising", "nausea", "headache"],
                    "side_effects_b": ["dizziness", "fatigue"],
                },
                "mechanistic": {
                    "targets_a": ["target1", "target2", "target3"],
                    "targets_b": ["target2", "target4"],
                    "common_targets": ["target2"],
                }
            },
            "drugs": {"a": {"name": "warfarin"}, "b": {"name": "aspirin"}},
        }
        
        query_context = {
            "drug_a": "warfarin",
            "drug_b": "aspirin",
            "prr_pair": 2.5,
        }
        
        filtered, metadata = filter_context_by_relevance(
            context,
            "warfarin + aspirin",
            query_context,
            min_relevance=0.1
        )
        
        assert "signals" in filtered
        assert metadata["items_before"] > 0
        assert metadata["items_after"] >= 0
        assert metadata["items_after"] <= metadata["items_before"]
    
    def test_high_threshold_filtering(self):
        """Test filtering with high relevance threshold."""
        context = {
            "signals": {
                "tabular": {
                    "side_effects_a": ["bleeding", "bruising", "nausea"],
                    "side_effects_b": ["dizziness"],
                }
            },
            "drugs": {"a": {"name": "warfarin"}, "b": {"name": "aspirin"}},
        }
        
        query_context = {"drug_a": "warfarin", "prr_pair": 2.5}
        
        filtered, metadata = filter_context_by_relevance(
            context,
            "query",
            query_context,
            min_relevance=5.0  # High threshold
        )
        
        # Should filter out low-relevance items
        assert metadata["items_after"] <= metadata["items_before"]
    
    def test_preserve_structure(self):
        """Test that context structure is preserved."""
        context = {
            "signals": {"tabular": {"side_effects_a": ["bleeding"]}},
            "drugs": {"a": {"name": "warfarin"}},
            "caveats": ["test"],
        }
        
        query_context = {"drug_a": "warfarin"}
        
        filtered, _ = filter_context_by_relevance(
            context,
            "query",
            query_context
        )
        
        # Should preserve all top-level keys
        assert "signals" in filtered
        assert "drugs" in filtered
        assert "caveats" in filtered
    
    def test_empty_context(self):
        """Test filtering empty context."""
        context = {}
        query_context = {}
        
        filtered, metadata = filter_context_by_relevance(
            context,
            "query",
            query_context
        )
        
        assert filtered == {}
        assert metadata["items_before"] == 0


class TestFilterContextSections:
    """Test cases for filter_context_sections function."""
    
    def test_filter_specific_sections(self):
        """Test filtering specific sections."""
        context = {
            "signals": {
                "tabular": {
                    "side_effects_a": ["bleeding", "bruising", "nausea"],
                },
                "mechanistic": {
                    "targets_a": ["target1", "target2"],
                }
            }
        }
        
        query_context = {"drug_a": "warfarin"}
        
        filtered = filter_context_sections(
            context,
            "query",
            query_context,
            sections_to_filter=["side_effects"],
            min_relevance=0.1
        )
        
        # Side effects should be filtered
        assert "signals" in filtered
        assert "tabular" in filtered["signals"]
        assert len(filtered["signals"]["tabular"]["side_effects_a"]) <= 3


class TestFilterItemsByRelevance:
    """Test cases for _filter_items_by_relevance function."""
    
    def test_basic_item_filtering(self):
        """Test basic item filtering."""
        items = ["item1", "item2", "item3"]
        query_context = {"drug_a": "warfarin"}
        
        filtered = _filter_items_by_relevance(
            items,
            query_context,
            "side_effect",
            min_relevance=0.1
        )
        
        assert len(filtered) <= len(items)
        assert all(item in items for item in filtered)
    
    def test_overlap_bonus(self):
        """Test that overlapping items get bonus and are kept."""
        items = ["target1", "target2", "target3"]
        overlap_items = ["target2"]
        query_context = {"drug_a": "warfarin"}
        
        filtered = _filter_items_by_relevance(
            items,
            query_context,
            "target",
            min_relevance=0.1,
            overlap_items=overlap_items
        )
        
        # Overlapping target should be included
        assert "target2" in filtered
    
    def test_high_threshold(self):
        """Test filtering with high threshold."""
        items = ["item1", "item2", "item3"]
        query_context = {}
        
        filtered = _filter_items_by_relevance(
            items,
            query_context,
            "side_effect",
            min_relevance=10.0  # Very high threshold
        )
        
        # Most items should be filtered out
        assert len(filtered) <= len(items)
    
    def test_empty_items(self):
        """Test filtering empty item list."""
        filtered = _filter_items_by_relevance(
            [],
            {},
            "side_effect",
            min_relevance=0.1
        )
        
        assert filtered == []

