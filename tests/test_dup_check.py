"""D3 — focused unit tests for semantic_duplicate_check (app/pipeline/stages.py).

Real signature: semantic_duplicate_check(reel_id: int) -> int | None.
Behavior under test:
- content-free guard: a reel whose summary strips to <40 chars never
  merges, even against a cos-1.0 identical reel-level vector
- merge: long summary + cosine > 0.93 against a completed, non-duplicate
  other reel returns that reel's id
- guards that must NOT merge: no own reel-level embedding, cosine below
  threshold, dimension mismatch, other reel not completed, other reel
  already marked duplicate_of.

DB rows are inserted directly; embeddings carry owner_id, text_used, dim,
model as the schema requires (owner_type='reel', owner_id=reel_id).
"""
import struct

LONG_SUMMARY = ("Complete guide to batch normalization and why it "
                "stabilizes deep network training")


def _add_reel(uid, *, summary="", status="completed", duplicate_of=None):
    from app.db.schema import get_db

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, status, summary,"
            " duplicate_of) VALUES (?,?,?,?,?)",
            (uid, "upload", status, summary, duplicate_of))
        return cur.lastrowid


def _add_reel_embedding(reel_id, vec):
    from app.db.schema import get_db

    blob = struct.pack(f"<{len(vec)}f", *vec)
    with get_db() as db:
        db.execute(
            "INSERT INTO embeddings(owner_type, owner_id, reel_id, text_used,"
            " dim, vector, model) VALUES ('reel', ?, ?, ?, ?, ?, 'test-embed')",
            (reel_id, reel_id, f"summary vector for reel {reel_id}",
             len(vec), blob))


def test_short_summary_identical_vector_is_not_duplicate(sample_user):
    from app.pipeline.stages import semantic_duplicate_check

    other = _add_reel(sample_user, summary="x" * 60)
    _add_reel_embedding(other, [0.5] * 8)
    mine = _add_reel(sample_user, summary="saved clip")  # 10 chars < 40
    _add_reel_embedding(mine, [0.5] * 8)  # cosine would be 1.0
    assert semantic_duplicate_check(mine) is None


def test_long_identical_summary_merges(sample_user):
    from app.pipeline.stages import semantic_duplicate_check

    other = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(other, [0.25] * 8)
    mine = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(mine, [0.25] * 8)
    assert semantic_duplicate_check(mine) == other


def test_cosine_below_threshold_returns_none(sample_user):
    from app.pipeline.stages import semantic_duplicate_check

    other = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(other, [0.0, 1.0] + [0.0] * 6)
    mine = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(mine, [1.0, 0.0] + [0.0] * 6)  # cos = 0.0
    assert semantic_duplicate_check(mine) is None


def test_no_own_embedding_returns_none(sample_user):
    from app.pipeline.stages import semantic_duplicate_check

    other = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(other, [0.25] * 8)
    mine = _add_reel(sample_user, summary=LONG_SUMMARY)  # no embedding row
    assert semantic_duplicate_check(mine) is None


def test_dim_mismatch_is_skipped(sample_user):
    from app.pipeline.stages import semantic_duplicate_check

    other = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(other, [0.25] * 4)  # 4-d vs mine's 8-d
    mine = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(mine, [0.25] * 8)
    assert semantic_duplicate_check(mine) is None


def test_non_completed_other_is_excluded(sample_user):
    from app.pipeline.stages import semantic_duplicate_check

    other = _add_reel(sample_user, summary=LONG_SUMMARY, status="queued")
    _add_reel_embedding(other, [0.25] * 8)
    mine = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(mine, [0.25] * 8)
    assert semantic_duplicate_check(mine) is None


def test_already_duplicate_other_is_excluded(sample_user):
    from app.pipeline.stages import semantic_duplicate_check

    original = _add_reel(sample_user, summary=LONG_SUMMARY)  # no embedding
    merged = _add_reel(sample_user, summary=LONG_SUMMARY,
                       duplicate_of=original)
    _add_reel_embedding(merged, [0.25] * 8)  # otherwise a perfect match
    mine = _add_reel(sample_user, summary=LONG_SUMMARY)
    _add_reel_embedding(mine, [0.25] * 8)
    assert semantic_duplicate_check(mine) is None
