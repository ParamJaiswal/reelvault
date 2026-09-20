"""SQLite schema + migrations.

Design notes
------------
- SQLite in WAL mode: single-writer is fine for a personal knowledge platform,
  zero-ops, and fast enough for this workload. All AI artifacts live here too.
- Evidence Ledger: every extracted fact row carries evidence_quote +
  evidence_timestamp + evidence_source (transcript|ocr|vision|metadata|caption).
- processing_events gives a full audit trail per reel stage transition.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.config import settings

SCHEMA_VERSION = 5

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        api_key_hash TEXT NOT NULL,
        settings_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS ingestion_sources (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        kind TEXT NOT NULL CHECK(kind IN ('url','file','watch_folder','share_target','instagram_official')),
        label TEXT NOT NULL DEFAULT '',
        config_json TEXT NOT NULL DEFAULT '{}',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS reels (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        source_kind TEXT NOT NULL,
        source_url TEXT,
        shortcode TEXT,
        author_handle TEXT,
        caption TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK(status IN ('queued','processing','completed','failed','duplicate')),
        current_stage TEXT NOT NULL DEFAULT 'ingest',
        progress REAL NOT NULL DEFAULT 0,
        error_code TEXT,
        error_message TEXT,
        duration_s REAL,
        language TEXT,
        media_path TEXT,
        thumb_path TEXT,
        content_hash TEXT,
        confidence REAL,
        summary TEXT,
        key_takeaways_json TEXT NOT NULL DEFAULT '[]',
        action_items_json TEXT NOT NULL DEFAULT '[]',
        categories_json TEXT NOT NULL DEFAULT '[]',
        priority INTEGER NOT NULL DEFAULT 0,
        starred INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        ai_cost_tokens INTEGER NOT NULL DEFAULT 0,
        process_ms INTEGER,
        duplicate_of INTEGER REFERENCES reels(id),
        ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_reels_user_status ON reels(user_id, status);
    CREATE INDEX IF NOT EXISTS idx_reels_shortcode ON reels(shortcode);
    CREATE INDEX IF NOT EXISTS idx_reels_hash ON reels(content_hash);
    CREATE INDEX IF NOT EXISTS idx_reels_ingested ON reels(user_id, ingested_at DESC);

    CREATE TABLE IF NOT EXISTS transcript_segments (
        id INTEGER PRIMARY KEY,
        reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
        start_s REAL NOT NULL,
        end_s REAL NOT NULL,
        text TEXT NOT NULL,
        avg_logprob REAL,
        no_speech_prob REAL
    );
    CREATE INDEX IF NOT EXISTS idx_segments_reel ON transcript_segments(reel_id, start_s);

    CREATE TABLE IF NOT EXISTS frames (
        id INTEGER PRIMARY KEY,
        reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
        t_s REAL NOT NULL,
        path TEXT NOT NULL,
        phash TEXT,
        selected_for_vision INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_frames_reel ON frames(reel_id, t_s);

    CREATE TABLE IF NOT EXISTS ocr_results (
        id INTEGER PRIMARY KEY,
        reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
        frame_id INTEGER REFERENCES frames(id) ON DELETE SET NULL,
        t_s REAL NOT NULL,
        text TEXT NOT NULL,
        conf REAL
    );
    CREATE INDEX IF NOT EXISTS idx_ocr_reel ON ocr_results(reel_id, t_s);

    CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        norm_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        display_name TEXT NOT NULL,
        attrs_json TEXT NOT NULL DEFAULT '{}',
        first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(user_id, norm_name, kind)
    );

    CREATE TABLE IF NOT EXISTS reel_entities (
        reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
        entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
        evidence_quote TEXT,
        evidence_t_s REAL,
        PRIMARY KEY (reel_id, entity_id)
    );

    -- The Evidence Ledger: one row per extracted structured fact.
    CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY,
        reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
        schema_type TEXT NOT NULL,          -- job | education | tool | event
        field TEXT NOT NULL,                -- salary, company, deadline...
        value TEXT NOT NULL,
        normalized_value TEXT,
        evidence_source TEXT NOT NULL DEFAULT 'transcript'
            CHECK(evidence_source IN ('transcript','ocr','vision','metadata','caption','assistant')),
        evidence_quote TEXT,
        evidence_t_s REAL,
        confidence REAL NOT NULL DEFAULT 0.5,
        user_corrected INTEGER NOT NULL DEFAULT 0,
        ai_value TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_facts_reel ON facts(reel_id);
    CREATE INDEX IF NOT EXISTS idx_facts_field ON facts(schema_type, field);

    CREATE TABLE IF NOT EXISTS embeddings (
        id INTEGER PRIMARY KEY,
        owner_type TEXT NOT NULL CHECK(owner_type IN ('reel','fact','segment','ocr')),
        owner_id INTEGER NOT NULL,
        reel_id INTEGER NOT NULL,
        text_used TEXT NOT NULL,
        dim INTEGER NOT NULL,
        vector BLOB NOT NULL,
        model TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_embed_owner ON embeddings(owner_type, owner_id);
    CREATE INDEX IF NOT EXISTS idx_embed_reel ON embeddings(reel_id);

    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY,
        reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
        stage TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK(status IN ('queued','running','done','failed','dead')),
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        run_after REAL NOT NULL DEFAULT 0,
        heartbeat REAL NOT NULL DEFAULT 0,
        payload_json TEXT NOT NULL DEFAULT '{}',
        last_error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, run_after);
    CREATE INDEX IF NOT EXISTS idx_jobs_reel ON jobs(reel_id);

    CREATE TABLE IF NOT EXISTS processing_events (
        id INTEGER PRIMARY KEY,
        reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
        stage TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'info',
        message TEXT NOT NULL,
        data_json TEXT,
        ts TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_events_reel ON processing_events(reel_id, id);

    CREATE TABLE IF NOT EXISTS ai_runs (
        id INTEGER PRIMARY KEY,
        reel_id INTEGER REFERENCES reels(id) ON DELETE SET NULL,
        task TEXT NOT NULL,
        backend TEXT NOT NULL,
        model TEXT NOT NULL,
        latency_ms INTEGER,
        tokens_in INTEGER,
        tokens_out INTEGER,
        ok INTEGER NOT NULL DEFAULT 1,
        error TEXT
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        reel_id INTEGER REFERENCES reels(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    2: """
    -- Full-text index over reels for keyword search (FTS5, external content)
    CREATE VIRTUAL TABLE IF NOT EXISTS reels_fts USING fts5(
        title, summary, caption, author_handle,
        content='reels', content_rowid='id'
    );
    CREATE TRIGGER IF NOT EXISTS reels_ai AFTER INSERT ON reels BEGIN
        INSERT INTO reels_fts(rowid, title, summary, caption, author_handle)
        VALUES (new.id, new.title, new.summary, new.caption, new.author_handle);
    END;
    CREATE TRIGGER IF NOT EXISTS reels_ad AFTER DELETE ON reels BEGIN
        INSERT INTO reels_fts(reels_fts, rowid, title, summary, caption, author_handle)
        VALUES ('delete', old.id, old.title, old.summary, old.caption, old.author_handle);
    END;
    CREATE TRIGGER IF NOT EXISTS reels_au AFTER UPDATE OF
        title, summary, caption, author_handle ON reels BEGIN
        INSERT INTO reels_fts(reels_fts, rowid, title, summary, caption, author_handle)
        VALUES ('delete', old.id, old.title, old.summary, old.caption, old.author_handle);
        INSERT INTO reels_fts(rowid, title, summary, caption, author_handle)
        VALUES (new.id, new.title, new.summary, new.caption, new.author_handle);
    END;
    INSERT INTO reels_fts(reels_fts) VALUES('rebuild');
    """,
    3: """
    -- Deadline intelligence columns (deterministic dates computed in code)
    ALTER TABLE reels ADD COLUMN deadline_iso TEXT;
    ALTER TABLE reels ADD COLUMN reminder_iso TEXT;
    ALTER TABLE reels ADD COLUMN deadline_raw TEXT;
    CREATE INDEX IF NOT EXISTS idx_reels_reminder ON reels(reminder_iso);
    """,
    4: """
    -- User accounts: roles + password credentials
    ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'viewer'
        CHECK(role IN ('owner','admin','viewer'));
    ALTER TABLE users ADD COLUMN password_hash TEXT;
    ALTER TABLE users ADD COLUMN password_salt TEXT;

    -- Revocable sessions w/ rotating refresh tokens + roles
    CREATE TABLE IF NOT EXISTS auth_sessions (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'viewer'
            CHECK(role IN ('owner','admin','viewer')),
        access_hash TEXT NOT NULL,
        refresh_hash TEXT NOT NULL,
        access_exp REAL NOT NULL,
        refresh_exp REAL NOT NULL,
        rotated_from INTEGER REFERENCES auth_sessions(id),
        revoked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_used TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_asess_access ON auth_sessions(access_hash);
    CREATE INDEX IF NOT EXISTS idx_asess_refresh ON auth_sessions(refresh_hash);
    CREATE INDEX IF NOT EXISTS idx_asess_user ON auth_sessions(user_id);

    -- Web Push subscriptions (one row per browser/device)
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        endpoint TEXT UNIQUE NOT NULL,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        user_agent TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    5: """
    -- Summary grounding. AGENTS.md sec.8 requires every model claim to be
    -- source-supported, but reels.summary is abstractive prose and was
    -- persisted unchecked (and indexed into reels_fts by migration 2).
    -- Ratio of the summary's claim terms found in the source spans; NULL
    -- means never measured (every pre-v5 row, including user-written ones
    -- until they are re-saved).
    ALTER TABLE reels ADD COLUMN summary_grounding REAL;
    """,
}


def connect(db_path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    p = db_path or settings.db_path
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(db_path: Path | None = None) -> int:
    """Apply pending migrations; returns final schema version."""
    with get_db() if db_path is None else _ctx(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        applied = {
            r["version"] for r in conn.execute("SELECT version FROM schema_migrations")
        }
        for version in sorted(MIGRATIONS):
            if version in applied:
                continue
            conn.executescript(MIGRATIONS[version])
            conn.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
            )
        return max(MIGRATIONS)


@contextmanager
def _ctx(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
