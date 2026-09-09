"""D1 — hybrid_search() blend ordering (app/knowledge/search.py).

Deterministic fixtures: keyword hits come from FTS (reels_fts is an
external-content table auto-filled by triggers on reels INSERT), semantic
hits from hand-packed 2-D vectors queried through a fixed-vector fake
embedder. Vector components are chosen so cosines round to clean values
(0.6 / 0.4 / 0.9 / 0.5) after the float32 blob round-trip.

Asserts reciprocal-rank-fusion sanity: a reel hit by BOTH sources
(blend = 1/kw_rank + sem_score) outranks keyword-only and semantic-only
hits, blend values match the formula, and the semantic-only tail is
ordered by cosine. Blend assertions are recomputed from returned values so
they hold under either bm25 tie order.
"""
import struct

import pytest


class _FakeEmbedder:
    """embed() returns one fixed vector regardless of input text."""

    def __init__(self, vec):
        self._vec = list(vec)

    def embed(self, texts, **kwargs):
        return [list(self._vec) for _ in texts]


def _add_reel(uid, title, summary, *, status="completed", confidence=0.8):
    from app.db.schema import get_db

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, status, title, summary,"
            " confidence) VALUES (?,?,?,?,?,?)",
            (uid, "upload", status, title, summary, confidence))
        return cur.lastrowid


def _add_reel_embedding(reel_id, vec):
    from app.db.schema import get_db

    blob = struct.pack(f"<{len(vec)}f", *vec)
    with get_db() as db:
        db.execute(
            "INSERT INTO embeddings(owner_type, owner_id, reel_id, text_used,"
            " dim, vector, model) VALUES ('reel', ?, ?, ?, ?, ?, 'test-embed')",
            (reel_id, reel_id, f"body of reel {reel_id}", len(vec), blob))


def test_both_sources_rank_above_single_source(sample_user):
    from app.knowledge.search import hybrid_search

    uid = sample_user
    both = _add_reel(uid, "Learn Python Fast",
                     "python basics, python idioms, python tooling")
    kw_only = _add_reel(uid, "Python Deep Dive", "advanced python patterns")
    sem_only = _add_reel(uid, "Orchid Care",
                         "how to repot orchids at home without killing them")
    _add_reel_embedding(both, [0.6, 0.8])            # cos(query)=0.6
    _add_reel_embedding(kw_only, [0.0, 1.0])         # cos=0.0 -> semantic skips it
    _add_reel_embedding(sem_only, [0.4, 0.916515])   # cos=0.4

    results = hybrid_search(uid, "python", _FakeEmbedder([1.0, 0.0]))

    assert [r["id"] for r in results] == [both, kw_only, sem_only]
    both_r, kw_r, sem_r = results
    # source membership
    assert both_r["kw_rank"] in (1, 2)
    assert both_r["sem_score"] == pytest.approx(0.6, abs=1e-6)
    assert kw_r["kw_rank"] in (1, 2)
    assert kw_r["sem_score"] is None
    assert sem_r["kw_rank"] is None
    assert sem_r["sem_score"] == pytest.approx(0.4, abs=1e-6)
    # blend formula (recomputed from returned values: bm25 tie order between
    # the two keyword hits must not change the expected values)
    assert both_r["blend"] == round(
        1.0 / both_r["kw_rank"] + both_r["sem_score"], 4)
    assert kw_r["blend"] == round(1.0 / kw_r["kw_rank"], 4)
    assert sem_r["blend"] == sem_r["sem_score"]
    # both-source reel beats every single-source reel, keyword-only
    # (1/kw_rank >= 0.5) still beats the 0.4 semantic-only hit
    assert both_r["blend"] > kw_r["blend"] > sem_r["blend"]


def test_semantic_only_ordering_by_cosine(sample_user):
    from app.knowledge.search import hybrid_search

    uid = sample_user
    high = _add_reel(uid, "Sourdough starter guide",
                     "feeding and stretching a sourdough starter daily")
    low = _add_reel(uid, "Orchid repotting",
                    "when and how to repot an orchid safely")
    _add_reel_embedding(high, [0.9, 0.43589])    # cos(query)=0.9
    _add_reel_embedding(low, [0.5, 0.866025])    # cos(query)=0.5

    results = hybrid_search(uid, "xylophonewidget",
                            _FakeEmbedder([1.0, 0.0]))

    assert [r["id"] for r in results] == [high, low]
    hi, lo = results
    assert hi["kw_rank"] is None and lo["kw_rank"] is None
    assert hi["sem_score"] == pytest.approx(0.9, abs=1e-6)
    assert lo["sem_score"] == pytest.approx(0.5, abs=1e-6)
    assert hi["blend"] == hi["sem_score"]
    assert lo["blend"] == lo["sem_score"]


def test_hybrid_no_matches_returns_empty(sample_user):
    from app.knowledge.search import hybrid_search

    results = hybrid_search(sample_user, "zzzqqqnothing",
                            _FakeEmbedder([1.0, 0.0]))
    assert results == []
