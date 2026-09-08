"""Reliability regressions: permanent failures dead-letter immediately,
retryable failures don't let downstream stages run between retries, and
unreadable media fails fast instead of burning the ffprobe timeout ladder.
Fails on pre-fix code (observed live: corrupt file burned 3x60s ffprobe
timeouts; a dead URL reel's media+transcribe retry-stormed together)."""
import subprocess
import time
from pathlib import Path

import pytest

from app.db.schema import get_db
from app.pipeline.media import MediaError, PermanentMediaError


def _reel_with_jobs(kind="upload", media_path=None):
    with get_db() as db:
        cur = db.execute("INSERT INTO users(username, display_name,"
                         " api_key_hash) VALUES ('t','Tester','')")
        uid = cur.lastrowid
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, status, source_url,"
            " media_path) VALUES (?,?,?,?,?)",
            (uid, kind, "queued",
             "https://www.instagram.com/p/Rel00000001/" if kind == "url"
             else None,
             media_path))
        reel_id = cur.lastrowid
    return reel_id


def test_permanent_error_deads_on_first_attempt(tmp_db):
    """A deterministic failure must skip the whole retry ladder."""
    from app.db.queue import PermanentJobError, Queue

    reel_id = _reel_with_jobs()
    q = Queue()
    q.enqueue(reel_id)
    q.register("media", lambda rid, p: (_ for _ in ()).throw(
        PermanentJobError("unreadable file")))

    with get_db() as db:
        db.execute("UPDATE jobs SET run_after=0")

    q.run_pending("test")

    with get_db() as db:
        reel = dict(db.execute("SELECT status, error_message FROM reels"
                               " WHERE id=?", (reel_id,)).fetchone())
        jobs = {r["stage"]: r["status"] for r in db.execute(
            "SELECT stage, status FROM jobs WHERE reel_id=?", (reel_id,))}
    assert reel["status"] == "failed"
    assert jobs["media"] == "dead"
    # downstream cancelled, and media did NOT burn its retry ladder
    assert jobs.get("transcribe", "missing") in ("dead", "missing")
    with get_db() as db:
        attempts = db.execute(
            "SELECT attempts FROM jobs WHERE reel_id=? AND stage='media'",
            (reel_id,)).fetchone()["attempts"]
    assert attempts == 1  # dead on the first attempt, not 3


def test_retryable_failure_delays_downstream(tmp_db, monkeypatch):
    """While media is in its backoff window, transcribe must not run —
    it can only fail on missing audio and retry-storm."""
    from app.core.config import settings
    from app.db.queue import Queue

    # other tests mutate settings globally; pin a real backoff so one
    # run_pending pass can't re-claim the failed job immediately
    monkeypatch.setattr(settings, "retry_backoff_s", 50)
    reel_id = _reel_with_jobs()
    q = Queue()
    q.enqueue(reel_id)
    calls = {"media": 0}

    def flaky_media(rid, p):
        calls["media"] += 1
        raise MediaError("disk hiccup")

    q.register("media", flaky_media)

    with get_db() as db:
        db.execute("UPDATE jobs SET run_after=0")

    # one worker pass: claims ONE due job (media), fails it
    q.run_pending("test")
    assert calls["media"] == 1

    with get_db() as db:
        media = dict(db.execute(
            "SELECT status, run_after FROM jobs WHERE reel_id=?"
            " AND stage='media'", (reel_id,)).fetchone())
        transcribe = dict(db.execute(
            "SELECT status, run_after FROM jobs WHERE reel_id=?"
            " AND stage='transcribe'", (reel_id,)).fetchone())
    assert media["status"] == "queued"
    # transcribe pushed out to at least media's next attempt time
    assert transcribe["run_after"] >= media["run_after"]


def test_media_stage_missing_input_is_permanent(tmp_db):
    """stage_media on a reel with no media must raise the permanent type
    (still catchable as MediaError for compatibility)."""
    from app.pipeline import stages

    reel_id = _reel_with_jobs(kind="url", media_path=None)
    with pytest.raises(PermanentMediaError) as exc:
        stages.stage_media(reel_id, {})
    assert isinstance(exc.value, MediaError)
    assert "No media on disk" in str(exc.value)


def test_transcribe_missing_audio_is_permanent(tmp_db):
    from app.pipeline import stages

    reel_id = _reel_with_jobs(media_path="D:/nowhere/fake.mp4")
    with pytest.raises(PermanentMediaError):
        stages.stage_transcribe(reel_id, {})


def test_truncated_video_fails_fast(tmp_db):
    """A corrupt (truncated) video must fail in seconds with the permanent
    type — not burn the 60s ffprobe timeout three times."""
    reel_id = _reel_with_jobs(media_path=None)
    mp = Path("media/video/_test_truncated.mp4")
    mp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=duration=1:size=160x120:rate=5",
         "-c:v", "libx264", str(mp)],
        check=True, capture_output=True)
    data = mp.read_bytes()
    mp.write_bytes(data[:1024])  # truncate: header only, no moov

    with get_db() as db:
        db.execute("UPDATE reels SET media_path=? WHERE id=?",
                   (str(mp), reel_id))

    from app.pipeline import stages
    from app.db.queue import StageCancelled
    t0 = time.monotonic()
    # stage_media converts permanent media errors to StageCancelled after
    # failing the reel (Phase 3 guard contract) — the permanent type is
    # what ffprobe/validate raise underneath
    with pytest.raises(StageCancelled) as exc:
        stages.stage_media(reel_id, {})
    elapsed = time.monotonic() - t0
    mp.unlink(missing_ok=True)
    assert "moov atom" in str(exc.value.__cause__)
    assert elapsed < 15, f"took {elapsed:.1f}s — retry ladder not skipped?"
    # and the reel is failed, downstream cancelled
    with get_db() as db:
        reel = dict(db.execute("SELECT status FROM reels WHERE id=?",
                               (reel_id,)).fetchone())
        queued = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE reel_id=? AND status='queued'",
            (reel_id,)).fetchone()[0]
    assert reel["status"] == "failed"
    assert queued == 0
