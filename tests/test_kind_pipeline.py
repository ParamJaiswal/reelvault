"""V2-4: Kind-aware pipeline tests.

Verifies content_kind column, STAGE_PLANS routing, and stage skip behavior
for text-only sources.
"""
import sqlite3


def test_migration_v7_adds_content_kind(tmp_db):
    """content_kind column exists with default 'video'."""
    db = sqlite3.connect(str(tmp_db))
    cols = {r[1]: r[4] for r in db.execute("PRAGMA table_info(reels)")}
    db.close()
    assert "content_kind" in cols
    assert cols["content_kind"] == "'video'"


def test_migration_v7_creates_documents_table(tmp_db):
    """documents table exists for text-first source bodies."""
    db = sqlite3.connect(str(tmp_db))
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    db.close()
    assert "documents" in tables


def test_stage_plans_video_has_all_stages():
    """Video keeps full v0.1 pipeline."""
    from app.db.queue import stages_for_kind
    plan = stages_for_kind("video")
    assert "media" in plan
    assert "transcribe" in plan
    assert "classify_extract" in plan
    assert "embed" in plan
    assert "finalize" in plan


def test_stage_plans_article_skips_media_transcribe():
    """Article sources skip media and transcribe."""
    from app.db.queue import stages_for_kind
    plan = stages_for_kind("article")
    assert "media" not in plan
    assert "transcribe" not in plan
    assert "classify_extract" in plan
    assert "embed" in plan


def test_stage_plans_x_post_includes_ocr():
    """X posts keep OCR for image attachments."""
    from app.db.queue import stages_for_kind
    plan = stages_for_kind("x_post")
    assert "media" not in plan
    assert "transcribe" not in plan
    assert "ocr" in plan
    assert "classify_extract" in plan


def test_stage_plans_unknown_kind_defaults_to_video():
    """Unknown content_kind falls back to video pipeline."""
    from app.db.queue import stages_for_kind
    plan = stages_for_kind("unknown_future_kind")
    assert plan == stages_for_kind("video")


def test_text_reel_default_content_kind_is_video(tmp_db):
    """Existing reels without explicit content_kind default to video."""
    from app.db.schema import get_db
    with get_db() as db:
        uid = db.execute(
            "INSERT INTO users(username, display_name, api_key_hash)"
            " VALUES('u','U','')").lastrowid
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url)"
            " VALUES(?,'upload','http://x')", (uid,)).lastrowid
        row = db.execute("SELECT content_kind FROM reels WHERE id=?", (rid,)).fetchone()
    assert row["content_kind"] == "video"


def test_document_body_indexed_in_fts(tmp_db):
    """Document body text is searchable via FTS."""
    from app.db.schema import get_db, refresh_fts

    with get_db() as db:
        uid = db.execute(
            "INSERT INTO users(username, display_name, api_key_hash)"
            " VALUES('u','U','')").lastrowid
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, source_url,"
            " title, status, current_stage)"
            " VALUES(?,'url','paper','http://arxiv.org/x',"
            " 'Attention Is All You Need','completed','done')", (uid,)).lastrowid
        db.execute(
            "INSERT INTO documents(reel_id, body_text, mime_type)"
            " VALUES(?,'transformer architecture self-attention mechanism',"
            " 'application/pdf')", (rid,))

    refresh_fts(rid)

    db = sqlite3.connect(str(tmp_db))
    hits = db.execute(
        "SELECT COUNT(*) FROM reels_fts WHERE reels_fts MATCH 'transformer'"
    ).fetchone()[0]
    db.close()
    assert hits >= 1
