"""V2 end-to-end: text source ingest → documents → kind-aware job plan →
document-evidenced fact → FTS search. LLM stages are not run (they need
llama-server; eval policy gates them separately)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.api.main import app, require_auth


@pytest.fixture()
def client(tmp_db, sample_user):
    app.dependency_overrides[require_auth] = lambda: sample_user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _syndication_mock(text="Hiring senior engineers, apply at careers.site"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "__typename": "Tweet",
        "text": text,
        "user": {"screen_name": "recruiter", "name": "Recruiter"},
        "mediaDetails": [],
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_x_ingest_creates_document_and_skips_media_stages(client, tmp_db):
    with patch("app.ingest.adapters.x_adapter.httpx.get",
               return_value=_syndication_mock()):
        res = client.post("/api/reels", json={
            "url": "https://x.com/recruiter/status/1234567890123"})
    assert res.status_code == 200, res.text
    rid = res.json()["reel_id"]

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    reel = db.execute("SELECT content_kind, author_handle FROM reels WHERE id=?",
                      (rid,)).fetchone()
    doc = db.execute("SELECT body_text FROM documents WHERE reel_id=?",
                     (rid,)).fetchone()
    stages = {r["stage"] for r in
              db.execute("SELECT stage FROM jobs WHERE reel_id=?", (rid,))}
    db.close()

    assert reel["content_kind"] == "x_post"
    assert reel["author_handle"] == "recruiter"
    assert "senior engineers" in doc["body_text"]
    # kind-aware plan: no media/transcribe for text posts
    assert "media" not in stages and "transcribe" not in stages
    assert {"classify_extract", "embed", "finalize"} <= stages


def test_document_fact_flows_to_fts_and_back(client, tmp_db):
    """A document-evidenced fact passes the CHECK and becomes searchable."""
    from app.db.schema import get_db, refresh_fts

    with get_db() as db:
        uid = db.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, title,"
            " status, current_stage)"
            " VALUES(?,'url','x_post','Post about Rust','completed','done')",
            (uid,)).lastrowid
        db.execute(
            "INSERT INTO documents(reel_id, body_text) VALUES(?,"
            " 'Rust ownership makes memory safety easy')", (rid,))
        db.execute(
            "INSERT INTO facts(reel_id, schema_type, field, value,"
            " evidence_source, evidence_quote, confidence)"
            " VALUES(?,'tool','tool_name','Rust','document',"
            " 'Rust ownership makes memory safety easy', 0.8)", (rid,))
    refresh_fts(rid)

    from app.knowledge.search import keyword_search
    hits = keyword_search(1, "Rust")
    assert any(h["id"] == rid for h in hits)


def test_instagram_url_still_routes_to_ig(client, tmp_db):
    """Regression: IG URLs must not be swallowed by the article adapter."""
    from app.ingest.adapters.base import route, IngestRequest
    req = IngestRequest(
        user_id=1, kind="url",
        url="https://www.instagram.com/reel/ABC12345/")
    adapter, _ = route(req)
    assert adapter.name == "url"


def test_instagr_am_short_link_routes_to_ig(client, tmp_db):
    from app.ingest.adapters.base import route, IngestRequest
    req = IngestRequest(
        user_id=1, kind="url",
        url="https://instagr.am/p/XYZ987654/")
    adapter, _ = route(req)
    assert adapter.name != "article"


def test_x_video_tweet_still_skips_ingest(client, tmp_db):
    """Regression: media-bearing tweets must not gain an ingest download job —
    a failed x.com download triggers the metadata-only terminal path and would
    clobber the stored document body."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "__typename": "Tweet",
        "text": "watch this demo",
        "user": {"screen_name": "dev", "name": "Dev"},
        "mediaDetails": [{"type": "video", "video_info": {"variants": [
            {"content_type": "video/mp4", "bitrate": 1000,
             "url": "https://video.x/demo.mp4"}]}}],
    }
    resp.raise_for_status = MagicMock()
    with patch("app.ingest.adapters.x_adapter.httpx.get", return_value=resp):
        res = client.post("/api/reels", json={
            "url": "https://x.com/dev/status/1234567890124"})
    assert res.status_code == 200
    rid = res.json()["reel_id"]
    db = sqlite3.connect(str(tmp_db))
    stages = {r[0] for r in
              db.execute("SELECT stage FROM jobs WHERE reel_id=?", (rid,))}
    doc = db.execute("SELECT body_text FROM documents WHERE reel_id=?",
                     (rid,)).fetchone()
    db.close()
    assert "ingest" not in stages and "media" not in stages
    assert doc is not None and "demo" in doc[0]


def test_stage_media_and_transcribe_skip_for_text(tmp_db, sample_user):
    """Worker stages must no-op (not fail) when run for a text reel."""
    from app.db.schema import get_db
    from app.pipeline.stages import stage_media, stage_transcribe

    with get_db() as db:
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, status,"
            " current_stage) VALUES(?,'url','article','processing','ingest')",
            (sample_user,)).lastrowid

    stage_media(rid, {})
    stage_transcribe(rid, {})

    with get_db() as db:
        st = db.execute("SELECT status FROM reels WHERE id=?", (rid,)).fetchone()
        events = [r[0] for r in db.execute(
            "SELECT message FROM processing_events WHERE reel_id=? AND stage='media'",
            (rid,)).fetchall()]
    assert st[0] == "processing"  # not failed
    assert any("skipped" in e for e in events)
