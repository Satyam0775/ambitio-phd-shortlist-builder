"""
FAISS in-memory index for researcher embeddings.
Uses IndexFlatIP (inner product) since embeddings are L2-normalized
→ inner product == cosine similarity.
"""

import logging
from typing import Optional
import numpy as np

from app.utils.logging import get_logger

logger = get_logger(__name__)

EMBEDDING_DIM = 384


class FAISSService:
    def __init__(self) -> None:
        self._index = None
        self._id_map: list[str] = []  # position → openalex_id
        self._initialized = False

    def _load_faiss(self):
        try:
            import faiss
            return faiss
        except ImportError:
            logger.error("faiss-cpu not installed.")
            raise

    def build_index(
        self,
        researcher_ids: list[str],
        embeddings: np.ndarray,
    ) -> None:
        """
        Build a flat inner-product FAISS index.
        embeddings: (n, 384) float32, L2-normalized.
        """
        faiss = self._load_faiss()
        if embeddings.shape[0] == 0:
            logger.warning("No embeddings provided — FAISS index not built.")
            return

        self._index = faiss.IndexFlatIP(EMBEDDING_DIM)
        self._index.add(embeddings)
        self._id_map = researcher_ids
        self._initialized = True
        logger.info("FAISS index built with %d vectors.", len(researcher_ids))

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 200,
    ) -> list[tuple[str, float]]:
        """
        Find top-k nearest researchers to the query embedding.
        Returns list of (openalex_id, similarity_score).
        """
        if not self._initialized or self._index is None:
            logger.warning("FAISS index not initialized.")
            return []

        faiss = self._load_faiss()
        query = query_embedding.reshape(1, -1).astype(np.float32)
        top_k = min(top_k, self._index.ntotal)
        distances, indices = self._index.search(query, top_k)

        results: list[tuple[str, float]] = []
        for idx, dist in zip(indices[0], distances[0]):
            if idx < 0 or idx >= len(self._id_map):
                continue
            researcher_id = self._id_map[idx]
            results.append((researcher_id, float(dist)))
        return results

    def get_similarity(
        self,
        query_embedding: np.ndarray,
        researcher_embedding: np.ndarray,
    ) -> float:
        """Direct cosine similarity between two L2-normalized vectors."""
        return float(np.dot(query_embedding, researcher_embedding))

    @property
    def size(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal
