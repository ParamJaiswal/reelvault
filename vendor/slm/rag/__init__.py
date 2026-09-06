"""Grounded RAG pipeline (Phase 5, Step 1).

Exports:
- ``Retriever``        -- hybrid TF-IDF + vector search over business knowledge.
- ``Document``         -- retrievable knowledge unit.
- ``InMemoryVectorStore`` -- lightweight hashed-trigram vector index.
- ``GroundingGate``    -- NLI-style check that responses are supported by
  retrieved evidence; ungrounded answers are suppressed.
"""

from slm.rag.grounding import DEFAULT_REFUSAL, GroundingGate, GroundingResult
from slm.rag.retriever import Document, InMemoryVectorStore, Retriever

__all__ = [
    "DEFAULT_REFUSAL",
    "Document",
    "GroundingGate",
    "GroundingResult",
    "InMemoryVectorStore",
    "Retriever",
]