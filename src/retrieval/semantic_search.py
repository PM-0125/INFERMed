# src/retrieval/semantic_search.py
# -*- coding: utf-8 -*-
"""
Semantic search module using embeddings for drug and side effect similarity.

This module provides:
- Drug name embedding and similarity search
- Side effect semantic search
- Vector index management with caching
"""

from __future__ import annotations

import os
import json
import hashlib
import importlib.util
import logging
import pickle
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

import numpy as np

HAS_EMBEDDINGS = importlib.util.find_spec("sentence_transformers") is not None


def _load_sentence_transformer():
    if not HAS_EMBEDDINGS:
        return None
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    return SentenceTransformer

LOG = logging.getLogger(__name__)

# Model configuration
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_CACHE_DIR = os.path.join("data", "cache", "embeddings")
SIMILARITY_THRESHOLD = float(os.getenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.5"))

# Ensure cache directory exists
os.makedirs(EMBEDDING_CACHE_DIR, exist_ok=True)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _LexicalFallbackEmbeddingModel:
    """Dependency-free embedding fallback for offline tests and restricted dev hosts."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def encode(self, texts: List[str], show_progress_bar: bool = False) -> np.ndarray:
        rows = [self._embed(text) for text in texts]
        return np.vstack(rows) if rows else np.zeros((0, self.dimensions), dtype=np.float32)

    def _embed(self, text: str) -> np.ndarray:
        value = (text or "").strip().lower()
        vec = np.zeros(self.dimensions, dtype=np.float32)
        features = self._features(value)
        if not features:
            vec[0] = 1.0
            return vec
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        norm = np.linalg.norm(vec)
        return vec / (norm + 1e-8)

    @staticmethod
    def _features(value: str) -> List[str]:
        tokens = re.findall(r"[a-z0-9]+", value)
        features = list(tokens)
        compact = "".join(tokens)
        for n in (3, 4, 5):
            if len(compact) >= n:
                features.extend(compact[i : i + n] for i in range(0, len(compact) - n + 1))
        return features


class SemanticSearcher:
    """
    Semantic search using sentence transformers for drug and side effect similarity.
    """
    
    def __init__(self, model_name: str = EMBEDDING_MODEL_NAME, cache_dir: str = EMBEDDING_CACHE_DIR):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.model = None
        self.drug_index: Dict[str, np.ndarray] = {}
        self.drug_names: List[str] = []
        self.side_effect_index: Dict[str, np.ndarray] = {}
        self.side_effect_names: List[str] = []
        self._initialized = False
        
        self._initialize_model()
    
    def _initialize_model(self):
        """Initialize the embedding model."""
        if self.model is not None:
            return
        
        try:
            sentence_transformer = _load_sentence_transformer()
            if sentence_transformer is not None:
                if _env_bool("SEMANTIC_LOCAL_FILES_ONLY", True):
                    self.model = sentence_transformer(self.model_name, local_files_only=True)
                else:
                    self.model = sentence_transformer(self.model_name)
                LOG.info(f"Initialized embedding model: {self.model_name}")
                return
            LOG.info("sentence-transformers is unavailable")
        except Exception as e:
            LOG.warning(f"Failed to initialize embedding model; using fallback if enabled: {e}")

        if _env_bool("SEMANTIC_OFFLINE_FALLBACK", True):
            self.model = _LexicalFallbackEmbeddingModel()
            LOG.info("Initialized offline lexical embedding fallback")
        else:
            self.model = None
    
    def build_drug_index(self, drug_names: List[str], force_rebuild: bool = False) -> bool:
        """
        Build embedding index for drug names.
        
        Args:
            drug_names: List of unique drug names to index
            force_rebuild: If True, rebuild even if cache exists
            
        Returns:
            True if index was built successfully
        """
        if self.model is None:
            return False
        
        cache_path = os.path.join(self.cache_dir, "drug_index.pkl")
        
        # Try to load from cache
        if not force_rebuild and os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as f:
                    cached = pickle.load(f)
                    self.drug_names = cached.get("names", [])
                    self.drug_index = cached.get("index", {})
                LOG.info(f"Loaded drug index from cache: {len(self.drug_names)} drugs")
                return True
            except Exception as e:
                LOG.warning(f"Failed to load drug index cache: {e}")
        
        # Build new index
        if not drug_names:
            LOG.warning("No drug names provided for indexing")
            return False
        
        try:
            LOG.info(f"Building drug index for {len(drug_names)} drugs...")
            # Remove duplicates while preserving order
            unique_drugs = list(dict.fromkeys(drug_names))
            self.drug_names = unique_drugs
            
            # Generate embeddings in batches for efficiency
            batch_size = 32
            embeddings = []
            for i in range(0, len(unique_drugs), batch_size):
                batch = unique_drugs[i:i + batch_size]
                batch_embeddings = self.model.encode(batch, show_progress_bar=False)
                embeddings.append(batch_embeddings)
            
            # Combine all embeddings
            all_embeddings = np.vstack(embeddings)
            
            # Normalize embeddings for cosine similarity
            norms = np.linalg.norm(all_embeddings, axis=1, keepdims=True)
            all_embeddings = all_embeddings / (norms + 1e-8)
            
            # Store in index
            self.drug_index = {name: emb for name, emb in zip(unique_drugs, all_embeddings)}
            
            # Save to cache
            try:
                with open(cache_path, "wb") as f:
                    pickle.dump({"names": self.drug_names, "index": self.drug_index}, f)
                LOG.info(f"Saved drug index to cache: {len(self.drug_names)} drugs")
            except Exception as e:
                LOG.warning(f"Failed to save drug index cache: {e}")
            
            self._initialized = True
            return True
            
        except Exception as e:
            LOG.error(f"Failed to build drug index: {e}")
            return False
    
    def search_similar_drugs(
        self, 
        query: str, 
        top_k: int = 10, 
        threshold: float = SIMILARITY_THRESHOLD
    ) -> List[Tuple[str, float]]:
        """
        Find similar drugs using semantic search.
        
        Args:
            query: Drug name to search for
            top_k: Number of results to return
            threshold: Minimum similarity score (0-1)
            
        Returns:
            List of (drug_name, similarity_score) tuples, sorted by similarity
        """
        if self.model is None or not self.drug_index:
            return []
        
        if not query or not query.strip():
            return []
        
        try:
            # Encode query
            query_embedding = self.model.encode([query], show_progress_bar=False)[0]
            query_norm = np.linalg.norm(query_embedding)
            if query_norm > 0:
                query_embedding = query_embedding / query_norm
            
            # Compute similarities
            similarities = []
            for drug_name, drug_embedding in self.drug_index.items():
                # Cosine similarity
                similarity = np.dot(query_embedding, drug_embedding)
                if similarity >= threshold:
                    similarities.append((drug_name, float(similarity)))
            
            # Sort by similarity (descending)
            similarities.sort(key=lambda x: x[1], reverse=True)
            
            return similarities[:top_k]
            
        except Exception as e:
            LOG.error(f"Semantic search failed: {e}")
            return []
    
    def build_side_effect_index(
        self, 
        side_effects: List[str], 
        force_rebuild: bool = False
    ) -> bool:
        """
        Build embedding index for side effects.
        
        Args:
            side_effects: List of unique side effect names to index
            force_rebuild: If True, rebuild even if cache exists
            
        Returns:
            True if index was built successfully
        """
        if self.model is None:
            return False
        
        cache_path = os.path.join(self.cache_dir, "side_effect_index.pkl")
        
        # Try to load from cache
        if not force_rebuild and os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as f:
                    cached = pickle.load(f)
                    self.side_effect_names = cached.get("names", [])
                    self.side_effect_index = cached.get("index", {})
                LOG.info(f"Loaded side effect index from cache: {len(self.side_effect_names)} effects")
                return True
            except Exception as e:
                LOG.warning(f"Failed to load side effect index cache: {e}")
        
        # Build new index
        if not side_effects:
            return False
        
        try:
            LOG.info(f"Building side effect index for {len(side_effects)} effects...")
            unique_effects = list(dict.fromkeys(side_effects))
            self.side_effect_names = unique_effects
            
            # Generate embeddings in batches
            batch_size = 32
            embeddings = []
            for i in range(0, len(unique_effects), batch_size):
                batch = unique_effects[i:i + batch_size]
                batch_embeddings = self.model.encode(batch, show_progress_bar=False)
                embeddings.append(batch_embeddings)
            
            all_embeddings = np.vstack(embeddings)
            norms = np.linalg.norm(all_embeddings, axis=1, keepdims=True)
            all_embeddings = all_embeddings / (norms + 1e-8)
            
            self.side_effect_index = {name: emb for name, emb in zip(unique_effects, all_embeddings)}
            
            # Save to cache
            try:
                with open(cache_path, "wb") as f:
                    pickle.dump({"names": self.side_effect_names, "index": self.side_effect_index}, f)
                LOG.info(f"Saved side effect index to cache: {len(self.side_effect_names)} effects")
            except Exception as e:
                LOG.warning(f"Failed to save side effect index cache: {e}")
            
            return True
            
        except Exception as e:
            LOG.error(f"Failed to build side effect index: {e}")
            return False
    
    def search_similar_side_effects(
        self, 
        query: str, 
        top_k: int = 10, 
        threshold: float = SIMILARITY_THRESHOLD
    ) -> List[Tuple[str, float]]:
        """
        Find similar side effects using semantic search.
        
        Args:
            query: Side effect name to search for
            top_k: Number of results to return
            threshold: Minimum similarity score (0-1)
            
        Returns:
            List of (side_effect_name, similarity_score) tuples
        """
        if self.model is None or not self.side_effect_index:
            return []
        
        if not query or not query.strip():
            return []
        
        try:
            query_embedding = self.model.encode([query], show_progress_bar=False)[0]
            query_norm = np.linalg.norm(query_embedding)
            if query_norm > 0:
                query_embedding = query_embedding / query_norm
            
            similarities = []
            for effect_name, effect_embedding in self.side_effect_index.items():
                similarity = np.dot(query_embedding, effect_embedding)
                if similarity >= threshold:
                    similarities.append((effect_name, float(similarity)))
            
            similarities.sort(key=lambda x: x[1], reverse=True)
            return similarities[:top_k]
            
        except Exception as e:
            LOG.error(f"Side effect semantic search failed: {e}")
            return []


# Global instance (lazy initialization)
_global_searcher: Optional[SemanticSearcher] = None


def get_semantic_searcher() -> Optional[SemanticSearcher]:
    """Get or create global semantic searcher instance."""
    global _global_searcher
    if _global_searcher is None:
        _global_searcher = SemanticSearcher()
    return _global_searcher

