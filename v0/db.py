"""v0 DB — one tiny table, stdlib sqlite3. Status is the queue."""
import sqlite3

from v0.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL DEFAULT 'queued',
  source TEXT, url TEXT, title TEXT,
  video_path TEXT, audio_path TEXT, frames_dir TEXT,
  error TEXT,
  created_at TEXT DEFAULT (datetime('now'))
)
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def create_reel(source: str, url: str | None = None) -> int:
    with connect() as db:
        cur = db.execute(
            "INSERT INTO reels(source, url, status) VALUES (?,?, 'queued')",
            (source, url))
        return int(cur.lastrowid)


def set_status(reel_id: int, status: str, error: str | None = None,
               **fields: str | None) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    args: list = list(fields.values())
    if error is not None:
        cols = ("error=?," if cols else "error=?") + cols
        args.insert(0, error[:500])
    sql = f"UPDATE reels SET status=?{(',' + cols) if cols else ''} WHERE id=?"
    with connect() as db:
        db.execute(sql, [status, *args, reel_id])


def get_reel(reel_id: int) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
        return dict(row) if row else None
