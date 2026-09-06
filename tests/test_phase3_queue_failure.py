"""Regression: a permanently dead job must cancel the reel's remaining
queued stages and mark the reel failed. Fails on pre-fix queue.py, where
downstream stages ran on missing artifacts (corrupt video reached
'completed'/'duplicate')."""
import time


import time

from app.db.schema import get_db


def _reel_with_jobs():
    with get_db() as db:
        cur = db.execute("INSERT INTO users(username, display_name,"
                         " api_key_hash) VALUES ('t','Tester','')")
        uid = cur.lastrowid
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, status)"
            " VALUES (?, 'upload', 'queued')", (uid,))
        reel_id = cur.lastrowid
    return reel_id


def test_dead_job_cancels_downstream_and_fails_reel(tmp_db):
    from app.db.queue import Queue

    reel_id = _reel_with_jobs()
    q = Queue()
    q.enqueue(reel_id)  # all stages after ingest

    # fail the 'media' job until it goes dead (max_attempts = 3)
    status = None
    for _ in range(3):
        with get_db() as db:
            media_job = db.execute(
                "SELECT id FROM jobs WHERE reel_id=? AND stage='media'",
                (reel_id,)).fetchone()["id"]
        status = q.fail(media_job, "MediaError: no video stream")
        # transient failures requeue with backoff — clear it to retry now
        if status == "queued":
            with get_db() as db:
                db.execute("UPDATE jobs SET run_after=0 WHERE id=?",
                           (media_job,))
    assert status == "dead"

    with get_db() as db:
        reel = dict(db.execute("SELECT status, error_message FROM reels"
                               " WHERE id=?", (reel_id,)).fetchone())
        jobs = [dict(r) for r in db.execute(
            "SELECT stage, status FROM jobs WHERE reel_id=?", (reel_id,))]

    assert reel["status"] == "failed"
    assert "no video stream" in (reel["error_message"] or "")
    remaining = {j["stage"]: j["status"] for j in jobs}
    assert remaining["media"] == "dead"
    for stage in ("transcribe", "ocr", "classify_extract", "embed",
                  "finalize"):
        assert stage not in remaining, (stage, remaining)


def test_stage_cancelled_dead_letters_without_failing_reel(tmp_db):
    """A stage refusing to run (upstream failure) dead-letters quietly and
    must NOT overwrite the reel status or retry."""
    from app.db.queue import Queue, StageCancelled

    reel_id = _reel_with_jobs()
    q = Queue()
    q.enqueue(reel_id, ["media"])

    def _boom(rid, payload):
        raise StageCancelled("reel failed upstream — stage cancelled")

    q.register("media", _boom)
    q.run_pending("w0")

    with get_db() as db:
        job = dict(db.execute(
            "SELECT status, last_error, attempts FROM jobs WHERE reel_id=?",
            (reel_id,)).fetchone())
        reel = dict(db.execute("SELECT status FROM reels WHERE id=?",
                               (reel_id,)).fetchone())
    assert job["status"] == "dead"
    assert "failed upstream" in job["last_error"]
    assert reel["status"] == "queued"  # not 'failed' by its own guard trip
