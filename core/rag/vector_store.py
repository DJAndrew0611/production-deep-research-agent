"""
SQLite-based vector store for embedding persistence.
Uses BLOB storage for numpy arrays — zero external vector database infrastructure needed.
"""
import sqlite3
import json
import numpy as np
from typing import List, Tuple, Optional, Dict, Any
import logging

logger = logging.getLogger("deep_research.rag.vector_store")


class SQLiteVectorStore:
    """
    Lightweight vector store backed by SQLite.
    Stores embeddings as BLOBs alongside document metadata.
    Performs cosine similarity search against L2-normalized embeddings.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_table()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_table(self):
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_embeddings (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    source_type TEXT DEFAULT 'unknown',
                    embedding BLOB NOT NULL,
                    dimension INTEGER NOT NULL,
                    session_id TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    metadata_json TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embeddings_session 
                ON document_embeddings(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embeddings_url 
                ON document_embeddings(url)
            """)
            conn.commit()
        finally:
            conn.close()

    def upsert(
        self,
        doc_id: str,
        url: str,
        title: str,
        content: str,
        embedding: np.ndarray,
        source_type: str = "unknown",
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Insert or update a document embedding."""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO document_embeddings
                   (id, url, title, content, source_type, embedding, dimension, session_id, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id, url, title, content, source_type,
                    embedding.tobytes(),
                    embedding.shape[0],
                    session_id,
                    json.dumps(metadata or {}, ensure_ascii=False)
                )
            )
            conn.commit()
        finally:
            conn.close()

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
        session_id: Optional[str] = None,
        min_similarity: float = 0.2
    ) -> List[Dict[str, Any]]:
        """
        Cosine similarity search over stored vectors.
        Returns top_k most similar documents above min_similarity threshold.
        """
        conn = self._get_conn()
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT id, url, title, content, source_type, embedding, dimension, metadata_json "
                    "FROM document_embeddings WHERE session_id = ?",
                    (session_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, url, title, content, source_type, embedding, dimension, metadata_json "
                    "FROM document_embeddings"
                ).fetchall()
        finally:
            conn.close()

        if not rows:
            return []

        results = []
        for row in rows:
            doc_id, url, title, content, source_type, emb_bytes, dim, meta_json = row
            stored_emb = np.frombuffer(emb_bytes, dtype=np.float32).reshape(-1)
            
            # Cosine similarity (both query and stored embeddings are L2-normalized)
            similarity = float(np.dot(query_embedding, stored_emb))
            
            if similarity >= min_similarity:
                results.append({
                    "id": doc_id,
                    "url": url,
                    "title": title,
                    "content": content,
                    "source_type": source_type,
                    "similarity": round(similarity, 4),
                    "metadata": json.loads(meta_json) if meta_json else {}
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    def count(self, session_id: Optional[str] = None) -> int:
        """Return the number of stored embeddings."""
        conn = self._get_conn()
        try:
            if session_id:
                row = conn.execute(
                    "SELECT COUNT(*) FROM document_embeddings WHERE session_id = ?",
                    (session_id,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM document_embeddings").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def delete_session(self, session_id: str):
        """Delete all embeddings for a given session."""
        conn = self._get_conn()
        try:
            conn.execute(
                "DELETE FROM document_embeddings WHERE session_id = ?",
                (session_id,)
            )
            conn.commit()
        finally:
            conn.close()
