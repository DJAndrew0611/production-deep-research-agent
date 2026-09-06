"""
Unit tests for the RAG (Retrieval-Augmented Generation) module.
Runs reliably with both sentence-transformers and numpy hashing fallback.
"""
import os
import unittest
import tempfile
import numpy as np

from core.rag.embeddings import EmbeddingService
from core.rag.vector_store import SQLiteVectorStore
from core.rag.retriever import SemanticRetriever
from schemas import EvidenceCorpus, EvidenceDocument


class TestRAGModule(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_vectors.db")
        self.embedding_service = EmbeddingService()
        self.vector_store = SQLiteVectorStore(db_path=self.db_path)
        self.retriever = SemanticRetriever(db_path=self.db_path)

    def test_embedding_service_dimensions_and_normalization(self):
        """Embedding output must be float32 array with L2 norm approximately 1.0."""
        text = "Deep research agent verifying multimodal AI claims"
        emb = self.embedding_service.embed(text)
        self.assertIsInstance(emb, np.ndarray)
        self.assertEqual(emb.shape[0], self.embedding_service.dimension)
        norm = np.linalg.norm(emb)
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_embedding_batch(self):
        """Batch embedding must return correct 2D numpy array."""
        texts = ["Text one", "Text two", "Text three"]
        embs = self.embedding_service.embed_batch(texts)
        self.assertEqual(embs.shape, (3, self.embedding_service.dimension))

    def test_vector_store_crud_and_search(self):
        """Vector store must insert, count, search by similarity, and delete sessions."""
        e1 = self.embedding_service.embed("OpenAI GPT-4o multimodal architecture")
        e2 = self.embedding_service.embed("Tokyo weather forecast sunny and warm")

        self.vector_store.upsert(
            doc_id="doc_ai",
            url="https://openai.com/gpt-4o",
            title="GPT-4o Architecture",
            content="Multimodal intelligence across audio, vision, and text.",
            embedding=e1,
            session_id="sess_1"
        )
        self.vector_store.upsert(
            doc_id="doc_weather",
            url="https://weather.com/tokyo",
            title="Tokyo Weather",
            content="Clear skies and sunny weather in Tokyo.",
            embedding=e2,
            session_id="sess_2"
        )

        self.assertEqual(self.vector_store.count(), 2)
        self.assertEqual(self.vector_store.count(session_id="sess_1"), 1)

        # Query relevant to AI
        q_emb = self.embedding_service.embed("multimodal architecture GPT")
        res = self.vector_store.search(q_emb, top_k=2)
        self.assertGreater(len(res), 0)
        self.assertEqual(res[0]["id"], "doc_ai")

        # Session cleanup
        self.vector_store.delete_session("sess_1")
        self.assertEqual(self.vector_store.count(session_id="sess_1"), 0)
        self.assertEqual(self.vector_store.count(), 1)

    def test_semantic_retriever_pipeline_integration(self):
        """Retriever must index an EvidenceCorpus and retrieve ungrounded evidence for claims."""
        corpus = EvidenceCorpus(
            task_id="test_task",
            documents=[
                EvidenceDocument(
                    url="https://deepmind.google/technologies/gemini",
                    title="Gemini 1.5 Pro",
                    content="Gemini 1.5 Pro features a 1-million token context window natively.",
                    domain="deepmind.google",
                    source_type="first_party_announcement"
                )
            ]
        )

        indexed_count = self.retriever.index_corpus(corpus, session_id="sess_rag")
        self.assertEqual(indexed_count, 1)

        # Retrieve for a claim
        claim_statement = "Gemini 1.5 supports a long context window of 1 million tokens"
        empty_corpus = EvidenceCorpus(task_id="empty")
        ev_docs = self.retriever.find_evidence_for_claim(claim_statement, empty_corpus, top_k=1)

        self.assertEqual(len(ev_docs), 1)
        self.assertEqual(ev_docs[0].url, "https://deepmind.google/technologies/gemini")
        self.assertIn("1-million", ev_docs[0].content)


if __name__ == "__main__":
    unittest.main()
