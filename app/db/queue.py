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
import time
from contextlib import contextmanager
from typing import Any, Callable

from app.core.config import settings
from app.db.schema import connect

log = logging.getLogger("rv.queue")


class StageCancelled(Exception):
    """Raised by a stage when the reel already failed upstream — the job is
    dead-lettered quietly instead of retried or treated as a reel error."""


STAGES = [
    "ingest",
    "media",
    "transcribe",
    "ocr",
    "classify_extract",
    "embed",
    "finalize",
]


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

    def fail(self, job_id: int, error: str) -> str:
        """Record failure; requeue with backoff or dead-letter."""
        now = time.time()
        with _q() as conn:
            row = conn.execute(
                "SELECT attempts, max_attempts, stage, reel_id FROM jobs WHERE id=?",
                (job_id,)).fetchone()
            if row is None:
                return "gone"
            attempts = (row["attempts"] or 0) + 1
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

    def run_pending(self, worker_id: str = "w0") -> int:
        """Process all currently runnable jobs. Returns count processed."""
        n = 0
        while True:
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
                handler(job["reel_id"], payload)
                self.complete(job["id"])
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
        return n

    def stats(self) -> dict[str, int]:
        with _q() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}
