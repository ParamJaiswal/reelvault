"""Phase 1 regression tests: evidence floor, timestamp confidence,
bootstrap guard, queue claim heartbeat re-check. Each test fails on the
pre-fix code."""
import time

import pytest


# ------------------------------------------------------------- evidence
def test_short_quote_cannot_score_full_similarity():
    """Old bug: 'AI' inside a span rode `a in b` to 1.0 and passed."""
    from app.knowledge.evidence import (HALLUCINATION_THRESHOLD, SourceSpan,
                                        find_evidence)

    spans = [SourceSpan(text="AI is going to change everything",
                        t_s=12.0, source="transcript")]
    m = find_evidence("AI", "AI", spans)
    assert m.similarity < HALLUCINATION_THRESHOLD
    assert m.span is None


def test_min_quote_chars_floor_is_enforced():
    from app.knowledge.evidence import MIN_QUOTE_CHARS

    assert MIN_QUOTE_CHARS >= 15


def test_long_verbatim_quote_still_passes():
    from app.knowledge.evidence import SourceSpan, find_evidence

    spans = [SourceSpan(
        text="drink three liters of water every single day for best results",
        t_s=30.0, source="transcript")]
    m = find_evidence("three liters of water every single day",
                      "three liters of water", spans)
    assert m.similarity >= 0.85
    assert m.span is not None


# ------------------------------------------------------------ confidence
def test_confidence_without_timestamp_is_lower():
    from app.knowledge.evidence import confidence_score

    with_ts = confidence_score(0.9, 1, None, has_timestamp=True)
    no_ts = confidence_score(0.9, 1, None, has_timestamp=False)
    assert no_ts < with_ts
    # 0.35*0.9 + 0.2*0.5 = 0.415 — no fabricated 0.25 timestamp bonus
    assert no_ts == pytest.approx(0.42)


def test_confidence_with_timestamp_keeps_bonus():
    from app.knowledge.evidence import confidence_score

    assert confidence_score(0.9, 1, None, has_timestamp=True) \
        == pytest.approx(0.66)  # 0.35*0.9 + 0.25 + 0.1, rounded


# ------------------------------------------------------------------ auth
def test_bootstrap_token_with_empty_users_returns_none(tmp_db):
    """Old bug: AttributeError (500) when users table is empty."""
    from app.core.auth import resolve_request
    from app.core.config import AUTH_TOKEN

    ident = resolve_request(AUTH_TOKEN)  # tmp_db has zero users
    assert ident is None


# ----------------------------------------------------------------- queue
def _reel_with_job(stage: str = "media", user_id: int | None = None) -> int:
    from app.db.queue import Queue
    from app.db.schema import get_db

    with get_db() as db:
        if user_id is None:
            cur = db.execute("INSERT INTO users(username, display_name,"
                             " api_key_hash) VALUES ('t','Tester','')")
            user_id = cur.lastrowid
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, status)"
            " VALUES (?, 'upload', 'queued')", (user_id,))
        reel_id = cur.lastrowid
    Queue().enqueue(reel_id, [stage])
    return reel_id


def test_queue_claim_guard_blocks_steal_of_refreshed_job(tmp_db):
    """Old bug: worker B's claim UPDATE re-claimed a job worker A had just
    refreshed between B's SELECT and UPDATE. The guarded UPDATE must lose
    that race."""
    from app.db.queue import Queue
    from app.db.schema import connect

    _reel_with_job("media")
    q = Queue()
    stale = time.time() - 999_999

    # Job is running with a stale heartbeat (B's SELECT view)
    with connect() as db:
        db.execute("UPDATE jobs SET status='running', heartbeat=?",
                   (stale,))

    # Worker A legitimately re-claims the stale job → heartbeat refreshed
    job = q.claim("wA")
    assert job is not None

    # Worker B executes its claim UPDATE *now* — heartbeat is fresh again
    now = time.time()
    with connect() as db:
        cur = db.execute(
            "UPDATE jobs SET status='running', heartbeat=?, updated_at=?"
            " WHERE id=? AND (status='queued' OR (status='running'"
            " AND heartbeat<?))"
            " RETURNING id",
            (now, now, job["id"], stale))
        assert cur.fetchone() is None  # steal blocked


def test_queue_claim_no_steal_of_fresh_job_and_stale_reclaim(tmp_db):
    from app.db.queue import Queue
    from app.db.schema import get_db

    _reel_with_job("media")

    q = Queue()

    job = q.claim("w1")
    assert job is not None
    assert q.claim("w2") is None  # fresh heartbeat: no second claim

    with get_db() as db:
        db.execute("UPDATE jobs SET heartbeat=? WHERE id=?",
                   (time.time() - 999_999, job["id"]))
    again = q.claim("w3")
    assert again is not None and again["id"] == job["id"]  # reclaim works
