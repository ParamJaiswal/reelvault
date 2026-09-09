"""Regression tests for the P0/P1 audit batch:
- A5: valid categories returned verbatim (e.g. "Personal Advice") must not
  be silently dropped for lacking an alias entry.
- A6: router and schemas share ONE VALID_CATEGORIES list.
- A8: missing-audio transcription failure writes a processing event.
- A4: reminder_for() clamps a past computed reminder to now+1h while the
  deadline is still ahead (early warning instead of deadline-day silence).
"""
from datetime import datetime, timedelta

from app.ai.router import _normalize_categories
from app.knowledge.deadlines import reminder_for
from app.knowledge.schemas import VALID_CATEGORIES as SCHEMAS_CATS


def test_a6_single_source_of_truth():
    from app.ai import router

    assert router.VALID_CATEGORIES is SCHEMAS_CATS


def test_a5_verbatim_valid_category_not_dropped():
    # "Personal Advice" has no alias entry; only "advice" did. The model
    # returning it verbatim must still land in the normalized output.
    assert "Personal Advice" in _normalize_categories(["Personal Advice"])
    assert "Personal Advice" in _normalize_categories(["personal advice."])
    # alias path still wins and is not broken
    assert _normalize_categories(["advice"]) == ["Personal Advice"]
    # truly unknown junk is still dropped
    assert _normalize_categories(["crypto bro wisdom"]) == []
    # case-insensitive match against the valid list for any entry
    assert _normalize_categories(["data science"]) == ["Data Science"]


def test_a4_reminder_clamps_past_computed_reminder():
    future = datetime.now() + timedelta(days=3)
    r = reminder_for(future, days_before=5)  # 2 days in the past
    now = datetime.now()
    assert now <= r <= future, "must clamp into [now, deadline]"
    # deadline far away: unchanged behavior
    far = datetime.now() + timedelta(days=30)
    assert reminder_for(far, days_before=5) <= far - timedelta(days=5) + timedelta(seconds=5)
    # already-past deadline: never schedules a future reminder
    past = datetime.now() - timedelta(days=2)
    assert reminder_for(past, days_before=5) <= datetime.now()


def test_a8_missing_audio_writes_processing_event(tmp_db, monkeypatch):
    import pytest

    from app.db.queue import PermanentJobError
    from app.db.schema import get_db
    from app.pipeline.stages import stage_transcribe

    with get_db() as db:
        cur = db.execute("INSERT INTO users(username, display_name, api_key_hash)"
                         " VALUES ('t','Tester','')")
        uid = cur.lastrowid
        cur = db.execute("INSERT INTO reels(user_id, source_kind, status)"
                         " VALUES (?, 'upload', 'processing')", (uid,))
        rid = cur.lastrowid

    with pytest.raises(PermanentJobError):
        stage_transcribe(rid, {})

    with get_db() as db:
        ev = db.execute(
            "SELECT stage, level, message, data_json FROM processing_events"
            " WHERE reel_id=? AND stage='transcribe'", (rid,)).fetchone()
    assert ev is not None, "missing-audio failure must leave an audit trail"
    assert "audio" in ev["message"].lower()
    assert "NO_AUDIO" in (ev["data_json"] or "")
