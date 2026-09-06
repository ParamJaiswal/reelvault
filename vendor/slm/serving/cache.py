"""Semantic caching: never spend CPU cycles on the same business task twice.

A dependency-light in-memory cache (numpy cosine similarity over hashed
character-trigram embeddings -- the same embedding family as the RAG vector
store, so no model download and no external vector DB):

- ``get(prompt)`` returns a cached payload when cosine similarity to a prior
  prompt exceeds ``similarity_threshold`` (default **0.98**) **and** the
  entry is younger than ``ttl_seconds`` (0 = immortal).
- Expired entries are evicted lazily on access plus opportunistically in
  bulk when the store grows past ``max_entries``.
- Prometheus metrics: ``slm_cache_hits_total``, ``slm_cache_misses_total``,
  ``slm_cache_lookup_seconds`` (lookup latency histogram).

Only idempotent routes should be cached; side-effectful tool executions must
bypass the cache (the router integration enforces this).
"""

from __future__ import annotations

import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from prometheus_client import Counter, Histogram

CACHE_HITS = Counter("slm_cache_hits_total", "Semantic cache hits")
CACHE_MISSES = Counter("slm_cache_misses_total", "Semantic cache misses")
CACHE_LOOKUP_SECONDS = Histogram(
    "slm_cache_lookup_seconds",
    "Cache lookup latency",
    buckets=(0.0001, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.05),
)

_TRIM_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")


@dataclass(slots=True)
class CacheEntry:
    """One cached answer."""

    key_prompt: str
    embedding: np.ndarray
    payload: Any
    created_ts: float


def embed_text(text: str, dim: int = 256) -> np.ndarray:
    """Deterministic normalized embedding over alphanumeric tokens.

    Punctuation/case-insensitive so near-duplicate business prompts map to
    nearly identical vectors.
    """
    vec = np.zeros(dim, dtype=np.float32)
    compact = _TRIM_RE.sub(" ", _PUNCT_RE.sub(" ", text.lower())).strip()
    for i in range(max(0, len(compact) - 2)):
        bucket = zlib.crc32(compact[i : i + 3].encode("utf-8")) % dim
        vec[bucket] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


class SemanticCache:
    """In-memory semantic response cache with TTL + similarity gating."""

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.98,
        ttl_seconds: float = 3600.0,
        max_entries: int = 2048,
        dim: int = 256,
    ) -> None:
        if not 0.0 < similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in (0, 1]")
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.dim = dim
        self._entries: list[CacheEntry] = []

    # ------------------------------------------------------------------ lookup

    def get(self, prompt: str) -> tuple[bool, Any]:
        """Return ``(hit, payload)`` for the semantically closest entry."""
        started = time.perf_counter()
        query = embed_text(prompt, self.dim)
        now = time.time()
        hit_payload: Any = None
        try:
            for entry in list(self._entries):
                expired = (
                    self.ttl_seconds > 0 and now - entry.created_ts > self.ttl_seconds
                )
                if expired:
                    self._entries.remove(entry)
                    continue
                sim = float(entry.embedding @ query)
                if sim >= self.similarity_threshold:
                    hit_payload = entry.payload
                    break
        finally:
            CACHE_LOOKUP_SECONDS.observe(time.perf_counter() - started)
        if hit_payload is not None:
            CACHE_HITS.inc()
            return True, hit_payload
        CACHE_MISSES.inc()
        return False, None

    def put(self, prompt: str, payload: Any) -> None:
        """Insert an answer for future semantic lookups."""
        self._evict_expired(force=True)
        self._entries.append(
            CacheEntry(
                key_prompt=prompt,
                embedding=embed_text(prompt, self.dim),
                payload=payload,
                created_ts=time.time(),
            )
        )
        while len(self._entries) > self.max_entries:
            self._entries.pop(0)  # drop oldest

    # ---------------------------------------------------------------- internal

    def _evict_expired(self, *, force: bool = False) -> None:
        if self.ttl_seconds <= 0 and not force:
            return
        now = time.time()
        self._entries = [
            e for e in self._entries
            if self.ttl_seconds <= 0 or now - e.created_ts <= self.ttl_seconds
        ]

    # ------------------------------------------------------------ introspection

    def stats(self) -> dict[str, float]:
        from prometheus_client import REGISTRY

        hits = misses = -1.0
        for metric in REGISTRY.collect():
            if metric.name == "slm_cache_hits":
                hits = sum(s.value for s in metric.samples)
            elif metric.name == "slm_cache_misses":
                misses = sum(s.value for s in metric.samples)
        total = hits + misses
        return {
            "entries": len(self._entries),
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total > 0 else 0.0,
        }

    def clear(self) -> None:
        self._entries.clear()


__all__ = [
    "CACHE_HITS",
    "CACHE_LOOKUP_SECONDS",
    "CACHE_MISSES",
    "CacheEntry",
    "SemanticCache",
    "embed_text",
]