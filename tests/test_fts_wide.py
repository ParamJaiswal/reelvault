"""V2-3: Widened FTS index tests.

Verifies migration 6 creates the new columns and backfill aggregates
facts/transcript/OCR into the FTS index.
"""
import sqlite3


def test_migration_v6_creates_wide_fts(tmp_db):
    """FTS table has facts_text, transcript_text, ocr_text columns."""
    db = sqlite3.connect(str(tmp_db))
    cols = [r[1] for r in db.execute("PRAGMA table_info(reels_fts)")]
    db.close()
    assert "facts_text" in cols
    assert "transcript_text" in cols
    assert "ocr_text" in cols


def test_refresh_fts_indexes_facts(tmp_db):
    """refresh_fts aggregates fact values into FTS."""
    from app.db.schema import get_db, refresh_fts

    with get_db() as db:
        uid = db.execute(
            "INSERT INTO users(username, display_name, api_key_hash)"
            " VALUES('u','U','')").lastrowid
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, status, current_stage)"
            " VALUES(?,'video','http://x', 'completed', 'done')", (uid,)).lastrowid
        db.execute(
            "INSERT INTO facts(reel_id, schema_type, field, value,"
            " evidence_source, confidence)"
            " VALUES(?,'job','company','AcmeCorp','metadata',0.8)",
            (rid,))

    refresh_fts(rid)

    db = sqlite3.connect(str(tmp_db))
    hits = db.execute(
        "SELECT COUNT(*) FROM reels_fts WHERE reels_fts MATCH 'AcmeCorp'"
    ).fetchone()[0]
    db.close()
    assert hits >= 1


def test_refresh_fts_indexes_transcript(tmp_db):
    """refresh_fts aggregates transcript segments into FTS."""
    from app.db.schema import get_db, refresh_fts

    with get_db() as db:
        uid = db.execute(
            "INSERT INTO users(username, display_name, api_key_hash)"
            " VALUES('u','U','')").lastrowid
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, status, current_stage)"
            " VALUES(?,'video','http://x', 'completed', 'done')", (uid,)).lastrowid
        db.execute(
            "INSERT INTO transcript_segments(reel_id, start_s, end_s, text)"
            " VALUES(?, 0.0, 2.0, 'quantum entanglement explained simply')",
            (rid,))

    refresh_fts(rid)

    db = sqlite3.connect(str(tmp_db))
    hits = db.execute(
        "SELECT COUNT(*) FROM reels_fts WHERE reels_fts MATCH 'quantum'"
    ).fetchone()[0]
    db.close()
    assert hits >= 1


def test_keyword_search_finds_fact_value(tmp_db):
    """keyword_search returns reels matched via facts_text column."""
    from app.db.schema import get_db, refresh_fts
    from app.knowledge.search import keyword_search

    with get_db() as db:
        uid = db.execute(
            "INSERT INTO users(username, display_name, api_key_hash)"
            " VALUES('u','U','')").lastrowid
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, title, status, current_stage)"
            " VALUES(?,'video','http://x','Test Reel','completed','done')", (uid,)).lastrowid
        db.execute(
            "INSERT INTO facts(reel_id, schema_type, field, value,"
            " evidence_source, confidence)"
            " VALUES(?,'tool','tool_name','SuperWidget','metadata',0.9)",
            (rid,))

    refresh_fts(rid)
    results = keyword_search(uid, "SuperWidget")
    assert any(r["id"] == rid for r in results)
