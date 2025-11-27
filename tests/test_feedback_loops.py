# tests/test_feedback_loops.py
"""
Unit tests for feedback loops module.
"""

import pytest
import os
import tempfile
import shutil
from src.utils.feedback_loops import FeedbackTracker, get_feedback_tracker


class TestFeedbackTracker:
    """Test cases for FeedbackTracker class."""
    
    @pytest.fixture
    def temp_feedback_dir(self):
        """Create temporary feedback directory."""
        temp_dir = tempfile.mkdtemp()
        feedback_file = os.path.join(temp_dir, "feedback.json")
        yield feedback_file
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def tracker(self, temp_feedback_dir):
        """Create FeedbackTracker instance."""
        return FeedbackTracker(feedback_file=temp_feedback_dir)
    
    def test_initialization(self, tracker):
        """Test tracker initialization."""
        assert tracker is not None
        assert tracker.feedback_history is not None
        assert "feedback" in tracker.feedback_history
    
    def test_record_positive_feedback(self, tracker, temp_feedback_dir):
        """Test recording positive feedback."""
        items = ["warfarin", "aspirin", "bleeding"]
        tracker.record_feedback(
            "warfarin + aspirin",
            items,
            "good",
            user_rating=0.9
        )
        
        # Check that feedback was recorded
        assert len(tracker.feedback_history["feedback"]) == 1
        feedback = tracker.feedback_history["feedback"][0]
        assert feedback["is_positive"] is True
        assert feedback["response_quality"] == "good"
    
    def test_record_negative_feedback(self, tracker):
        """Test recording negative feedback."""
        items = ["drug1", "drug2"]
        tracker.record_feedback(
            "query",
            items,
            "bad",
            user_rating=0.2
        )
        
        feedback = tracker.feedback_history["feedback"][0]
        assert feedback["is_positive"] is False
    
    def test_item_score_tracking(self, tracker):
        """Test that item scores are tracked."""
        items = ["warfarin", "aspirin"]
        
        # Record positive feedback
        tracker.record_feedback("query1", items, "good")
        
        # Scores should increase
        score_warfarin = tracker.get_item_score("warfarin")
        score_aspirin = tracker.get_item_score("aspirin")
        
        assert score_warfarin > 1.0
        assert score_aspirin > 1.0
    
    def test_item_score_decrease(self, tracker):
        """Test that negative feedback decreases scores."""
        items = ["warfarin"]
        
        # Record negative feedback multiple times
        for _ in range(3):
            tracker.record_feedback("query", items, "bad")
        
        score = tracker.get_item_score("warfarin")
        # Should be less than default (1.0)
        assert score < 1.0
    
    def test_adjust_retrieval_weights(self, tracker):
        """Test adjusting retrieval weights based on feedback."""
        items = ["item1", "item2", "item3"]
        base_scores = [1.0, 1.0, 1.0]
        
        # Record positive feedback for item1
        tracker.record_feedback("query", ["item1"], "good")
        
        # Adjust weights
        adjusted = tracker.adjust_retrieval_weights(items, base_scores)
        
        # item1 should have higher weight
        assert adjusted[0] > adjusted[1]
        assert adjusted[0] > adjusted[2]
    
    def test_get_feedback_stats(self, tracker):
        """Test getting feedback statistics."""
        tracker.record_feedback("query1", ["item1"], "good")
        tracker.record_feedback("query2", ["item2"], "bad")
        tracker.record_feedback("query3", ["item3"], "good")
        
        stats = tracker.get_feedback_stats()
        
        assert stats["total_feedback"] == 3
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert stats["positive_ratio"] == pytest.approx(2/3, abs=0.01)
        assert stats["tracked_items"] >= 3
    
    def test_persistence(self, tracker, temp_feedback_dir):
        """Test that feedback persists across instances."""
        tracker.record_feedback("query", ["item1"], "good")
        
        # Create new tracker instance
        new_tracker = FeedbackTracker(feedback_file=temp_feedback_dir)
        
        # Should have loaded previous feedback
        assert len(new_tracker.feedback_history["feedback"]) == 1
        assert new_tracker.get_item_score("item1") > 1.0


class TestGetFeedbackTracker:
    """Test cases for get_feedback_tracker function."""
    
    def test_get_feedback_tracker(self):
        """Test getting global tracker instance."""
        tracker = get_feedback_tracker()
        assert tracker is not None
        assert isinstance(tracker, FeedbackTracker)
    
    def test_singleton(self):
        """Test that get_feedback_tracker returns same instance."""
        tracker1 = get_feedback_tracker()
        tracker2 = get_feedback_tracker()
        assert tracker1 is tracker2

