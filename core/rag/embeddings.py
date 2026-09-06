"""
Embedding generation service supporting SentenceTransformer with deterministic lightweight fallback.
"""
import logging
import hashlib
import numpy as np
from typing import List, Optional

logger = logging.getLogger("deep_research.rag.embeddings")


class EmbeddingService:
    """
    Dual-engine embedding service:
    1. Primary: sentence-transformers (e.g. all-MiniLM-L6-v2, 384 dim).
    2. Fallback: Normalized Hashing Vectorizer (pure numpy, 384 dim), ensures zero-crash
       offline execution and testing when heavy ML dependencies are absent.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", dimension: int = 384):
        self.model_name = model_name
        self.target_dimension = dimension
        self._model = None
        self._is_neural = False
        self._init_backend()

    def _init_backend(self):
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            self._is_neural = True
            logger.info(f"Loaded neural embedding model: {self.model_name}")
        except Exception:
            self._model = None
            self._is_neural = False
            logger.info("sentence-transformers not available; operating in deterministic hashing fallback mode.")

    @property
    def is_neural(self) -> bool:
        return self._is_neural

    @property
    def dimension(self) -> int:
        if self._is_neural and self._model is not None:
            return self._model.get_sentence_embedding_dimension()
        return self.target_dimension

    def _hash_embed(self, text: str) -> np.ndarray:
        """Deterministic, normalized token hashing vector (fallback engine)."""
        vec = np.zeros(self.target_dimension, dtype=np.float32)
        words = [w.strip().lower() for w in text.split() if w.strip()]
        if not words:
            return vec

        for word in words:
            # 32-bit hash mapped to dimension
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % self.target_dimension
            sign = 1.0 if (h >> 16) % 2 == 0 else -1.0
            vec[idx] += sign

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def embed(self, text: str) -> np.ndarray:
        """Generate a single normalized embedding vector."""
        if self._is_neural and self._model is not None:
            emb = self._model.encode(text, normalize_embeddings=True)
            return np.array(emb, dtype=np.float32)
        return self._hash_embed(text)

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> np.ndarray:
        """Generate normalized embedding vectors for a batch of texts."""
        if self._is_neural and self._model is not None:
            embs = self._model.encode(texts, normalize_embeddings=True, batch_size=batch_size, show_progress_bar=False)
            return np.array(embs, dtype=np.float32)
        return np.array([self._hash_embed(t) for t in texts], dtype=np.float32)
