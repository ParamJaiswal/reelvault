"""Hybrid retrieval scaffolding: TF-IDF keyword matching + vector similarity.

Deliberately dependency-light (numpy + standard library only) to preserve the
CPU-first, easily deployable ethos of the ecosystem:

- **Keyword leg:** classic TF-IDF scoring with sublinear term frequency.
- **Vector leg:** ``InMemoryVectorStore`` embeds text as hashed character
  trigram counts (CRC32-bucketed, L2-normalized) and ranks by cosine
  similarity -- no external embedding model or database client required.

The final ranking blends both legs: ``score = alpha * tfidf + (1 - alpha) * cosine``.
"""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(slots=True)
class Document:
    """A single retrievable unit of business knowledge."""

    doc_id: str
    text: str
    metadata: dict = field(default_factory=dict)


class InMemoryVectorStore:
    """Fixed-dim hashed char-trigram embeddings with cosine search.

    Embeddings are deterministic across processes (CRC32 bucketing) and need
    no model download. Intended for small corpora (hundreds of chunks), which
    is the target scale for on-prem business automation.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim < 16:
            raise ValueError("vector dim must be >= 16")
        self.dim = dim
        self.doc_ids: list[str] = []
        self._matrix = np.zeros((0, dim), dtype=np.float32)

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        compact = re.sub(r"\s+", " ", text.lower())
        for i in range(max(0, len(compact) - 2)):
            gram = compact[i : i + 3]
            bucket = zlib.crc32(gram.encode("utf-8")) % self.dim
            vec[bucket] += 1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def add(self, doc_id: str, text: str) -> None:
        if doc_id in self.doc_ids:
            raise ValueError(f"duplicate doc_id in vector store: {doc_id}")
        self.doc_ids.append(doc_id)
        row = self.embed(text)[None, :]
        self._matrix = np.vstack([self._matrix, row])

    def search(self, query: str, k: int = 3) -> list[tuple[str, float]]:
        """Return up to ``k`` ``(doc_id, cosine_similarity)`` pairs, best first."""
        if not self.doc_ids:
            return []
        sims = self._matrix @ self.embed(query)
        order = np.argsort(-sims)[:k]
        return [(self.doc_ids[i], round(float(sims[i]), 4)) for i in order]


class Retriever:
    """Hybrid retriever combining TF-IDF keyword scores with vector scores."""

    def __init__(
        self,
        *,
        vector_dim: int = 256,
        alpha: float = 0.5,
    ) -> None:
        if not 0.0 <= alpha <= 1.0:
            raise ValueError("alpha must be within [0, 1]")
        self.alpha = alpha
        self.docs: list[Document] = []
        self._term_freqs: list[Counter[str]] = []
        self._doc_freq: Counter[str] = Counter()
        self.vector_store = InMemoryVectorStore(dim=vector_dim)

    # ---------------------------------------------------------------- indexing

    def add_documents(self, docs: list[dict | Document]) -> None:
        """Index documents given as ``Document`` or ``{"doc_id", "text", "metadata"?}``."""
        for raw in docs:
            doc = raw if isinstance(raw, Document) else Document(
                doc_id=str(raw["doc_id"]),
                text=str(raw["text"]),
                metadata=dict(raw.get("metadata") or {}),
            )
            if any(d.doc_id == doc.doc_id for d in self.docs):
                raise ValueError(f"duplicate doc_id: {doc.doc_id}")
            tf = Counter(_tokenize(doc.text))
            self.docs.append(doc)
            self._term_freqs.append(tf)
            for term in tf:
                self._doc_freq[term] += 1
            self.vector_store.add(doc.doc_id, doc.text)

    def __len__(self) -> int:
        return len(self.docs)

    # ----------------------------------------------------------------- search

    def _tfidf_scores(self, query_terms: list[str]) -> dict[int, float]:
        n_docs = max(1, len(self.docs))
        scores: dict[int, float] = {}
        for idx, tf in enumerate(self._term_freqs):
            total = 0.0
            for term in query_terms:
                if term not in tf:
                    continue
                idf = math.log((n_docs + 1) / (self._doc_freq[term] + 1)) + 1.0
                total += (1.0 + math.log(tf[term])) * idf
            if total > 0:
                scores[idx] = total
        return scores

    def retrieve(self, query: str, k: int = 3) -> list[tuple[Document, float]]:
        """Return up to ``k`` ``(document, hybrid_score)`` pairs, best first."""
        query_terms = _tokenize(query)
        if not self.docs or not query_terms:
            return []
        kw = self._tfidf_scores(query_terms)
        kw_max = max(kw.values()) if kw else 0.0
        vec_hits = dict(self.vector_store.search(query, k=len(self.docs)))
        combined: list[tuple[int, float]] = []
        for idx in range(len(self.docs)):
            s_kw = (kw.get(idx, 0.0) / kw_max) if kw_max > 0 else 0.0
            s_vec = float(vec_hits.get(self.docs[idx].doc_id, 0.0))
            combined.append((idx, self.alpha * s_kw + (1.0 - self.alpha) * s_vec))
        combined.sort(key=lambda pair: -pair[1])
        return [(self.docs[i], round(s, 4)) for i, s in combined[:k]]

    def retrieve_texts(self, query: str, k: int = 3) -> list[str]:
        """Convenience: just the document texts, best first (for prompting)."""
        return [doc.text for doc, _ in self.retrieve(query, k=k)]