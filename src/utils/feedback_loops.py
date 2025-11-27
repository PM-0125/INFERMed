# src/utils/feedback_loops.py
# -*- coding: utf-8 -*-
"""
Feedback loops module for learning from user interactions.

This module provides functionality to track which retrieved items
lead to accurate responses and adjust retrieval weights accordingly.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

# Feedback storage
FEEDBACK_DIR = os.path.join("data", "cache", "feedback")
os.makedirs(FEEDBACK_DIR, exist_ok=True)

FEEDBACK_FILE = os.path.join(FEEDBACK_DIR, "feedback_history.json")


class FeedbackTracker:
    """
    Tracks feedback on retrieval quality and response accuracy.
    """
    
    def __init__(self, feedback_file: str = FEEDBACK_FILE):
        self.feedback_file = feedback_file
        self.feedback_history: Dict[str, Any] = self._load_feedback()
        self.item_scores: Dict[str, float] = defaultdict(lambda: 1.0)  # Default score of 1.0
        self._update_item_scores()
    
    def _load_feedback(self) -> Dict[str, Any]:
        """Load feedback history from disk."""
        if os.path.exists(self.feedback_file):
            try:
                with open(self.feedback_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                LOG.warning(f"Failed to load feedback: {e}")
                return {"feedback": [], "item_scores": {}}
        return {"feedback": [], "item_scores": {}}
    
    def _save_feedback(self):
        """Save feedback history to disk."""
        try:
            # Update item scores in history
            self.feedback_history["item_scores"] = dict(self.item_scores)
            
            with open(self.feedback_file, "w", encoding="utf-8") as f:
                json.dump(self.feedback_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            LOG.error(f"Failed to save feedback: {e}")
    
    def _update_item_scores(self):
        """Update item scores based on feedback history."""
        feedback_list = self.feedback_history.get("feedback", [])
        
        # Count positive and negative feedback per item
        item_feedback = defaultdict(lambda: {"positive": 0, "negative": 0})
        
        for feedback in feedback_list:
            items = feedback.get("retrieved_items", [])
            is_positive = feedback.get("is_positive", False)
            
            for item in items:
                if is_positive:
                    item_feedback[item]["positive"] += 1
                else:
                    item_feedback[item]["negative"] += 1
        
        # Calculate scores: positive feedback increases, negative decreases
        for item, counts in item_feedback.items():
            total = counts["positive"] + counts["negative"]
            if total > 0:
                # Score ranges from 0.5 to 2.0 based on feedback ratio
                ratio = counts["positive"] / total
                self.item_scores[item] = 0.5 + (ratio * 1.5)
        
        # Load saved scores
        saved_scores = self.feedback_history.get("item_scores", {})
        for item, score in saved_scores.items():
            if item not in self.item_scores or saved_scores[item] > self.item_scores[item]:
                self.item_scores[item] = score
    
    def record_feedback(
        self,
        query: str,
        retrieved_items: List[str],
        response_quality: str,  # "good", "bad", "neutral"
        user_rating: Optional[float] = None,  # Optional 0-1 rating
        context: Optional[Dict[str, Any]] = None
    ):
        """
        Record feedback on a query-response pair.
        
        Args:
            query: Original query
            retrieved_items: List of items that were retrieved
            response_quality: Quality assessment ("good", "bad", "neutral")
            user_rating: Optional numeric rating (0-1)
            context: Optional context dictionary for debugging
        """
        is_positive = response_quality.lower() in ("good", "positive", "accurate")
        
        feedback_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "query": query,
            "retrieved_items": retrieved_items,
            "response_quality": response_quality,
            "is_positive": is_positive,
            "user_rating": user_rating,
        }
        
        if context:
            feedback_entry["context"] = context
        
        # Add to history
        if "feedback" not in self.feedback_history:
            self.feedback_history["feedback"] = []
        
        self.feedback_history["feedback"].append(feedback_entry)
        
        # Update item scores
        for item in retrieved_items:
            if is_positive:
                # Increase score slightly
                self.item_scores[item] = min(2.0, self.item_scores[item] * 1.1)
            else:
                # Decrease score slightly
                self.item_scores[item] = max(0.5, self.item_scores[item] * 0.9)
        
        # Save feedback
        self._save_feedback()
    
    def get_item_score(self, item: str) -> float:
        """
        Get the learned score for an item.
        
        Args:
            item: Item identifier (drug name, side effect, etc.)
            
        Returns:
            Score (higher = more reliable based on feedback)
        """
        return self.item_scores.get(item, 1.0)
    
    def adjust_retrieval_weights(
        self,
        items: List[str],
        base_scores: Optional[List[float]] = None
    ) -> List[float]:
        """
        Adjust retrieval scores based on learned item scores.
        
        Args:
            items: List of item identifiers
            base_scores: Optional base scores to adjust
            
        Returns:
            Adjusted scores
        """
        if base_scores is None:
            base_scores = [1.0] * len(items)
        
        adjusted = []
        for item, base_score in zip(items, base_scores):
            item_score = self.get_item_score(item)
            adjusted.append(base_score * item_score)
        
        return adjusted
    
    def get_feedback_stats(self) -> Dict[str, Any]:
        """Get statistics about feedback history."""
        feedback_list = self.feedback_history.get("feedback", [])
        
        total = len(feedback_list)
        positive = sum(1 for f in feedback_list if f.get("is_positive", False))
        negative = total - positive
        
        return {
            "total_feedback": total,
            "positive": positive,
            "negative": negative,
            "positive_ratio": positive / total if total > 0 else 0.0,
            "tracked_items": len(self.item_scores),
        }


# Global feedback tracker
_global_tracker: Optional[FeedbackTracker] = None


def get_feedback_tracker() -> FeedbackTracker:
    """Get or create global feedback tracker instance."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = FeedbackTracker()
    return _global_tracker

