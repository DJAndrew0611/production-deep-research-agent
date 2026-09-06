"""
Semantic retriever combining embedding search with the existing EvidenceCorpus.
Bridges RAG capabilities into the existing Claim-Evidence pipeline.
"""
import uuid
import logging
from typing import List, Optional, Dict, Any

from schemas import EvidenceCorpus, EvidenceDocument
from core.rag.embeddings import EmbeddingService
from core.rag.vector_store import SQLiteVectorStore

logger = logging.getLogger("deep_research.rag.retriever")


class SemanticRetriever:
    """
    High-level retriever that:
    1. Indexes EvidenceDocuments from the pipeline into the vector store
    2. Performs semantic similarity search for gap-driven retrieval
    3. Returns results as EvidenceDocuments compatible with the existing pipeline
    """

    def __init__(
        self,
        db_path: str,
        model_name: str = "all-MiniLM-L6-v2"
    ):
        self.embedding_service = EmbeddingService(model_name=model_name)
        self.vector_store = SQLiteVectorStore(db_path=db_path)

    def index_corpus(
        self,
        corpus: EvidenceCorpus,
        session_id: Optional[str] = None
    ) -> int:
        """
        Index all documents from an EvidenceCorpus into the vector store.
        Returns the number of documents indexed.
        """
        indexed = 0
        for doc in corpus.documents:
            text_to_embed = f"{doc.title}. {doc.content[:500]}" if doc.title else doc.content[:500]
            if not text_to_embed.strip():
                continue

            embedding = self.embedding_service.embed(text_to_embed)
            doc_id = f"doc_{uuid.uuid4().hex[:12]}"

            self.vector_store.upsert(
                doc_id=doc_id,
                url=doc.url,
                title=doc.title or "",
                content=doc.content[:2000],
                embedding=embedding,
                source_type=getattr(doc, "source_type", "unknown"),
                session_id=session_id,
                metadata={"domain": getattr(doc, "domain", "")}
            )
            indexed += 1

        logger.info(f"Indexed {indexed} documents into vector store (session={session_id})")
        return indexed

    def search_similar(
        self,
        query: str,
        top_k: int = 5,
        session_id: Optional[str] = None,
        min_similarity: float = 0.2
    ) -> List[Dict[str, Any]]:
        """
        Search for documents semantically similar to the query.
        Returns ranked results with similarity scores.
        """
        query_embedding = self.embedding_service.embed(query)
        results = self.vector_store.search(
            query_embedding=query_embedding,
            top_k=top_k,
            session_id=session_id,
            min_similarity=min_similarity
        )
        return results

    def find_evidence_for_claim(
        self,
        claim_statement: str,
        corpus: EvidenceCorpus,
        top_k: int = 3,
        session_id: Optional[str] = None
    ) -> List[EvidenceDocument]:
        """
        Find evidence documents semantically relevant to a specific claim.
        Returns EvidenceDocument objects compatible with the existing pipeline.
        """
        results = self.search_similar(
            query=claim_statement,
            top_k=top_k,
            session_id=session_id,
            min_similarity=0.2
        )

        evidence_docs = []
        for r in results:
            if not corpus.contains_url(r["url"]):
                doc = EvidenceDocument(
                    url=r["url"],
                    title=r["title"],
                    content=r["content"],
                    domain=r.get("metadata", {}).get("domain", ""),
                    source_type=r.get("source_type", "unknown")
                )
                evidence_docs.append(doc)

        return evidence_docs
