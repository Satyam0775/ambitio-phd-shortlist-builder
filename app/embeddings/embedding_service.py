"""
Embedding service using sentence-transformers/all-MiniLM-L6-v2.
Generates normalized 384-dim vectors for semantic similarity.
"""

import logging
from typing import Optional
import numpy as np

from app.utils.logging import get_logger

logger = get_logger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingService:
    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _load_model(self):
        if self._model is None:
            logger.info("Loading embedding model: %s", MODEL_NAME)
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(MODEL_NAME)
                logger.info("Embedding model loaded successfully.")
            except ImportError:
                logger.error("sentence-transformers not installed.")
                raise
        return self._model

    def encode(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        """
        Encode a list of texts into L2-normalized embedding vectors.
        Returns np.ndarray of shape (n, 384).
        """
        model = self._load_model()
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)

        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return embeddings.astype(np.float32)

    def encode_single(self, text: str) -> np.ndarray:
        """Encode a single text string. Returns 1D array of shape (384,)."""
        result = self.encode([text])
        return result[0]

    def cosine_similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """Compute cosine similarity between two L2-normalized vectors."""
        # Since vectors are already L2-normalized, dot product == cosine similarity
        return float(np.dot(vec_a, vec_b))

    def batch_cosine_similarity(
        self, query: np.ndarray, matrix: np.ndarray
    ) -> np.ndarray:
        """
        Compute cosine similarities of query (384,) against matrix (n, 384).
        Returns array of shape (n,).
        """
        return matrix @ query
