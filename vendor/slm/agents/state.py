"""Durable agent/workflow state backed by local SQLite (Phase 10, Step 4).

Complex business automation (e.g. waiting days for a human reviewer) cannot
live in process memory. This module keeps the dependency footprint at the
Python standard library: ``sqlite3`` with WAL journaling.

- :class:`DurableWorkflowStore` -- save/load/update/list paused workflow
  states. The whole graph state is serialized as JSON, so any Python process
  (or a restarted pod with a mounted volume) can resume exactly where the
  workflow paused.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_ts  REAL NOT NULL,
    updated_ts  REAL NOT NULL,
    state_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
"""


class DurableWorkflowStore:
    """Thread-safe SQLite-backed store for paused workflow states."""

    def __init__(self, db_path: str | Path = "artifacts/workflow_state.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=15
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ writes

    def save(self, workflow_id: str, state: dict[str, Any]) -> None:
        now = time.time()
        payload = json.dumps(state, ensure_ascii=False, default=str)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO workflows (workflow_id, status, created_ts, updated_ts,"
                " state_json) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET status=excluded.status,"
                " updated_ts=excluded.updated_ts, state_json=excluded.state_json",
                (workflow_id, str(state.get("status", "paused")), now, now, payload),
            )

    def update_status(self, workflow_id: str, status: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE workflows SET status=?, updated_ts=? WHERE workflow_id=?",
                (status, time.time(), workflow_id),
            )
            return cursor.rowcount > 0

    def delete(self, workflow_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM workflows WHERE workflow_id=?", (workflow_id,)
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------- reads

    def load(self, workflow_id: str) -> dict[str, Any] | None:
        """Return the deserialized state dict, or ``None`` if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json FROM workflows WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def full_row(self, workflow_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT workflow_id, status, created_ts, updated_ts, state_json"
                " FROM workflows WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "workflow_id": row[0], "status": row[1],
            "created_ts": row[2], "updated_ts": row[3],
            "state": json.loads(row[4]),
        }

    def list_pending(self, status: str = "awaiting_human_review") -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT workflow_id, created_ts, state_json FROM workflows"
                " WHERE status=? ORDER BY created_ts",
                (status,),
            ).fetchall()
        return [
            {
                "workflow_id": r[0],
                "created_ts": r[1],
                "state": json.loads(r[2]),
            }
            for r in rows
        ]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["DurableWorkflowStore"]