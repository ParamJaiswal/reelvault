"""Durable SQLite-backed job queue.

Thread-safety: every operation opens its OWN connection (SQLite WAL allows
concurrent readers; writes serialize via SQLite's locking). No shared
connection objects cross threads.

- atomic claiming via UPDATE ... RETURNING
- retries with exponential backoff, dead-letter after max_attempts
- heartbeats so crashed workers' stale jobs are reclaimed
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

from app.core.config import settings
from app.db.schema import connect

log = logging.getLogger("rv.queue")

# C2: the WAL file grows until a checkpoint runs; TRUNCATE-checkpointing
# every N completed jobs keeps it bounded during long worker runs.
WAL_CHECKPOINT_EVERY_JOBS = 100


class StageCancelled(Exception):
    """Raised by a stage when the reel already failed upstream — the job is
    dead-lettered quietly instead of retried or treated as a reel error."""


class PermanentJobError(Exception):
    """Deterministic stage failure (missing input, unreadable media):
    retrying the same job can never succeed, so dead-letter immediately
    instead of burning the retry ladder (observed: a corrupt file burned
    3x60s ffprobe timeouts; a never-downloaded URL reel failed 3x)."""


STAGES = [
    "ingest",
    "media",
    "transcribe",
    "ocr",
    "classify_extract",
    "embed",
    "finalize",
]

# Kind-aware stage plans. Video keeps full pipeline; text sources skip
# media/transcribe. OCR stays available for image-bearing posts and PDFs.
STAGE_PLANS: dict[str, list[str]] = {
    "video": ["media", "transcribe", "ocr", "classify_extract", "embed", "finalize"],
    "x_post": ["ocr", "classify_extract", "embed", "finalize"],
    "article": ["classify_extract", "embed", "finalize"],
    "paper": ["ocr", "classify_extract", "embed", "finalize"],
    "note": ["classify_extract", "embed", "finalize"],
    "linkedin_post": ["ocr", "classify_extract", "embed", "finalize"],
    "image_post": ["ocr", "classify_extract", "embed", "finalize"],
}


def stages_for_kind(content_kind: str) -> list[str]:
    return STAGE_PLANS.get(content_kind, STAGE_PLANS["video"])


@contextmanager
def _q():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class Queue:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[int, dict], None]] = {}
        self._completed_since_checkpoint = 0

    def close(self) -> None:  # kept for API compatibility
        pass

    # -- producer API -------------------------------------------------
    def enqueue(self, reel_id: int, stages: list[str] | None = None) -> None:
        now = time.time()
        stages = stages or STAGES[1:]  # default: everything after ingest
        with _q() as conn:
            for s in stages:
                conn.execute(
                    "INSERT INTO jobs(reel_id, stage, status, run_after,"
                    " created_at, updated_at) VALUES (?,?,'queued',?,?,?)",
                    (reel_id, s, now, now, now),
                )
        log.info("enqueued reel=%s stages=%s", reel_id, stages)

    def retry_stage(self, reel_id: int, stage: str) -> bool:
        now = time.time()
        with _q() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='queued', attempts=0, run_after=?,"
                " updated_at=?, last_error=NULL"
                " WHERE reel_id=? AND stage=? AND status IN ('failed','dead')",
                (now, now, reel_id, stage),
            )
            return cur.rowcount > 0

    # -- consumer API --------------------------------------------------
    def claim(self, worker_id: str) -> dict[str, Any] | None:
        now = time.time()
        stale_cutoff = now - settings.stale_job_timeout_s
        with _q() as conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id FROM jobs WHERE status='queued' AND run_after<=?"
                " UNION"
                " SELECT id FROM jobs WHERE status='running' AND heartbeat<?",
                (now, stale_cutoff),
            ).fetchall()
            for r in rows:
                cur.execute(
                    "UPDATE jobs SET status='running', heartbeat=?, updated_at=?"
                    " WHERE id=? AND (status='queued' OR (status='running'"
                    " AND heartbeat<?))"
                    " RETURNING id, reel_id, stage, attempts, max_attempts, payload_json",
                    (now, now, r["id"], stale_cutoff),
                )
                claimed = cur.fetchone()
                if claimed:
                    return {
                        "id": claimed["id"],
                        "reel_id": claimed["reel_id"],
                        "stage": claimed["stage"],
                        "attempts": claimed["attempts"],
                        "max_attempts": claimed["max_attempts"],
                        "payload_json": claimed["payload_json"],
                    }
        return None

    def heartbeat(self, job_id: int) -> None:
        with _q() as conn:
            conn.execute("UPDATE jobs SET heartbeat=? WHERE id=?",
                         (time.time(), job_id))

    def complete(self, job_id: int) -> None:
        with _q() as conn:
            conn.execute(
                "UPDATE jobs SET status='done', updated_at=? WHERE id=?",
                (time.time(), job_id))

    def fail(self, job_id: int, error: str,
             permanent: bool = False) -> str:
        """Record failure; requeue with backoff or dead-letter.

        permanent=True skips the retry ladder: the failure is
        deterministic, so the job dead-letters on the first attempt
        (with the usual dead-letter side effects: reel failed, queued
        downstream jobs deleted)."""
        now = time.time()
        with _q() as conn:
            row = conn.execute(
                "SELECT attempts, max_attempts, stage, reel_id FROM jobs WHERE id=?",
                (job_id,)).fetchone()
            if row is None:
                return "gone"
            attempts = (row["attempts"] or 0) + 1
            if permanent and attempts < row["max_attempts"]:
                # dead-letter now, but record the true attempt count —
                # inflating it would fake a retry history that never happened
                row = dict(row)
                row["max_attempts"] = attempts
            if attempts >= row["max_attempts"]:
                status = "dead"
                run_after = now
                # A permanently dead stage poisons everything after it: running
                # downstream stages on missing artifacts produced garbage
                # (corrupt videos reaching 'completed'/'duplicate').
                conn.execute(
                    "UPDATE reels SET status='failed', error_message=?,"
                    " completed_at=? WHERE id=?",
                    (error[:500], time.strftime("%Y-%m-%d %H:%M:%S"),
                     row["reel_id"]))
                conn.execute(
                    "DELETE FROM jobs WHERE reel_id=? AND status='queued' AND id!=?",
                    (row["reel_id"], job_id))
            else:
                status = "queued"
                run_after = now + settings.retry_backoff_s * (2 ** (attempts - 1))
                # Downstream stages must not run between this job's retries:
                # they would fail on missing input and retry-storm alongside
                # the real failure (observed: transcribe failing repeatedly
                # while media was still in its backoff window).
                conn.execute(
                    "UPDATE jobs SET run_after=MAX(run_after,?), updated_at=?"
                    " WHERE reel_id=? AND id>? AND status='queued'",
                    (run_after, now, row["reel_id"], job_id))
            conn.execute(
                "UPDATE jobs SET status=?, attempts=?, run_after=?, updated_at=?,"
                " last_error=?, heartbeat=0 WHERE id=?",
                (status, attempts, run_after, now, error[:2000], job_id))
        log.warning("job failed id=%s stage=%s attempt=%s -> %s err=%s",
                    job_id, row["stage"], attempts, status, error[:200])
        return status

    # -- runner --------------------------------------------------------
    def register(self, stage: str, handler: Callable[[int, dict], None]) -> None:
        self.handlers[stage] = handler

    def _record_stage_timing(self, reel_id: int, stage: str,
                             duration_s: float) -> None:
        """Best-effort per-stage duration event (C1). Purely observational:
        must never fail a job that just completed (e.g. reel row deleted by
        a concurrent merge would violate the processing_events FK)."""
        try:
            with _q() as conn:
                conn.execute(
                    "INSERT INTO processing_events(reel_id, stage, level,"
                    " message, data_json) VALUES (?,?,'info',?,?)",
                    (reel_id, stage, f"stage completed in {duration_s:.2f}s",
                     json.dumps({"duration_s": round(duration_s, 3)})))
        except Exception:  # noqa: BLE001 - timing must not fail the job
            log.debug("stage timing not recorded reel=%s stage=%s",
                      reel_id, stage, exc_info=True)

    def _wal_checkpoint(self) -> None:
        try:
            with _q() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            log.info("WAL checkpoint (TRUNCATE) after %d completed jobs",
                     WAL_CHECKPOINT_EVERY_JOBS)
        except Exception:  # noqa: BLE001 - checkpoint is opportunistic
            log.warning("WAL checkpoint failed", exc_info=True)

    def _maybe_wal_checkpoint(self) -> None:
        if self._completed_since_checkpoint < WAL_CHECKPOINT_EVERY_JOBS:
            return
        self._completed_since_checkpoint = 0
        self._wal_checkpoint()

    def run_pending(self, worker_id: str = "w0",
                    stop_event: threading.Event | None = None) -> int:
        """Process all currently runnable jobs. Returns count processed.

        stop_event: when set, no NEW job is claimed once the current one
        finishes — lets the API worker exit cleanly between jobs on app
        shutdown (C3) instead of picking up unbounded further work."""
        n = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            job = self.claim(worker_id)
            if not job:
                break
            n += 1
            handler = self.handlers.get(job["stage"])
            payload = json.loads(job["payload_json"] or "{}")
            if handler is None:
                self.fail(job["id"], f"no handler for stage {job['stage']}")
                continue
            try:
                self.heartbeat(job["id"])
                t0 = time.monotonic()
                handler(job["reel_id"], payload)
                self.complete(job["id"])
                self._record_stage_timing(job["reel_id"], job["stage"],
                                          time.monotonic() - t0)
                self._completed_since_checkpoint += 1
            except PermanentJobError as e:
                # Deterministic failure: skip the retry ladder entirely.
                log.warning("permanent failure reel=%s stage=%s: %s",
                            job["reel_id"], job["stage"], str(e)[:150])
                self.fail(job["id"], f"{type(e).__name__}: {e}",
                          permanent=True)
            except StageCancelled as e:
                # Reel already failed upstream; kill this job and anything
                # still queued behind it. Not a new reel error.
                with _q() as conn:
                    conn.execute(
                        "UPDATE jobs SET status='dead', last_error=?,"
                        " updated_at=? WHERE id=?",
                        (str(e)[:2000], time.time(), job["id"]))
                    conn.execute(
                        "DELETE FROM jobs WHERE reel_id=? AND status='queued'",
                        (job["reel_id"],))
                log.info("stage cancelled reel=%s stage=%s",
                         job["reel_id"], job["stage"])
            except Exception as e:  # noqa: BLE001 - worker boundary
                log.exception("handler error stage=%s", job["stage"])
                self.fail(job["id"], f"{type(e).__name__}: {e}")
        self._maybe_wal_checkpoint()
        return n

    def stats(self) -> dict[str, int]:
        with _q() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}
