"""Phase 7 regression tests: URL reels must actually run the ingest
(download) stage. Regression: enqueue() defaults to STAGES[1:], so URL
ingests never enqueued 'ingest' — download_reel never ran and the media
stage died with "No media on disk" (observed live with a real reel URL)."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import app, require_auth


@pytest.fixture()
def client(tmp_db, sample_user):
    app.dependency_overrides[require_auth] = lambda: sample_user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _stages_for(client, url):
    res = client.post("/api/reels", json={"url": url})
    assert res.status_code == 200, res.text
    assert res.json()["duplicate"] is False
    rid = res.json()["reel_id"]
    from app.db.schema import get_db
    with get_db() as db:
        return rid, [r["stage"] for r in db.execute(
            "SELECT stage FROM jobs WHERE reel_id=? ORDER BY id", (rid,))]


def test_url_ingest_enqueues_ingest_stage(client, monkeypatch):
    """A URL reel's job chain must start with the ingest download stage."""
    monkeypatch.setattr("app.pipeline.fetch.download_reel",
                        lambda url, sc: {"path": "x.mp4", "caption": "",
                                         "author_handle": "", "title": ""})
    rid, stages = _stages_for(client, "https://www.instagram.com/p/Ph7url01/")
    assert stages[0] == "ingest", stages
    assert "media" in stages and "finalize" in stages
    # the reel row has no media_path; only the ingest stage can provide it
    from app.db.schema import get_db
    with get_db() as db:
        mp = db.execute("SELECT media_path FROM reels WHERE id=?",
                        (rid,)).fetchone()["media_path"]
    assert mp is None


def test_upload_ingest_does_not_enqueue_ingest_stage(client):
    """Uploads already have media on disk — no ingest stage needed
    (keeps the original upload path unchanged)."""
    import io
    r = client.post("/api/reels/upload",
                    files={"file": ("u.mp4", io.BytesIO(b"x"), "video/mp4")})
    assert r.status_code == 200, r.text
    rid = r.json()["reel_id"]
    from app.db.schema import get_db
    with get_db() as db:
        stages = [x["stage"] for x in db.execute(
            "SELECT stage FROM jobs WHERE reel_id=? ORDER BY id", (rid,))]
    assert "ingest" not in stages


def test_retry_of_url_reel_rebuilds_from_ingest(client, tmp_db, sample_user):
    """A URL reel that failed downstream (no media) must retry from the
    ingest stage, not from media — media can never succeed without a
    download."""
    from app.db.schema import get_db
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " status, current_stage, media_path)"
            " VALUES (?,?,?,?,?,?,NULL)",
            (sample_user, "url", "https://www.instagram.com/p/Rtry9x01/",
             "Rtry9x01", "failed", "media"))
        rid = cur.lastrowid
    res = client.post(f"/api/admin/retry/{rid}")
    assert res.status_code == 200, res.text
    assert res.json()["retrying"][0] == "ingest", res.text


def test_retry_dead_media_without_media_rebuilds_from_ingest(
        client, tmp_db, sample_user):
    """Dead media job + no media on disk + URL source: retrying media
    alone would dead-letter again — chain must rebuild from ingest
    (observed live on a real broken URL reel)."""
    from app.db.schema import get_db
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " status, current_stage, media_path)"
            " VALUES (?,?,?,?,?,?,NULL)",
            (sample_user, "url", "https://www.instagram.com/p/Rtry7x03/",
             "Rtry7x03", "failed", "media"))
        rid = cur.lastrowid
        db.execute(
            "INSERT INTO jobs(reel_id, stage, status, attempts, run_after,"
            " created_at, updated_at)"
            " VALUES (?,?,'dead',3,0,datetime('now'),datetime('now'))",
            (rid, "media"))
    res = client.post(f"/api/admin/retry/{rid}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["retrying"][0] == "ingest", body
    assert "media" in body["retrying"]
    with get_db() as db:
        stages = [x["stage"] for x in db.execute(
            "SELECT stage FROM jobs WHERE reel_id=? ORDER BY id", (rid,))]
    assert stages[0] == "ingest" and len(stages) == 7, stages


def test_retry_dead_media_with_media_keeps_media(client, tmp_db, sample_user):
    """Upload reels (media on disk) keep the old retry semantics."""
    from app.db.schema import get_db
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " status, current_stage, media_path)"
            " VALUES (?,?,?,?,?,?,?)",
            (sample_user, "file", "https://www.instagram.com/p/Rtry6x04/",
             "Rtry6x04", "failed", "media", "D:/nowhere/u.mp4"))
        rid = cur.lastrowid
        db.execute(
            "INSERT INTO jobs(reel_id, stage, status, attempts, run_after,"
            " created_at, updated_at)"
            " VALUES (?,?,'dead',3,0,datetime('now'),datetime('now'))",
            (rid, "media"))
    res = client.post(f"/api/admin/retry/{rid}")
    assert res.status_code == 200, res.text
    assert res.json()["retrying"] == ["media"], res.text


def test_retry_of_upload_reel_keeps_media_start(client, tmp_db, sample_user):
    """Uploads (media present) keep rebuilding from the failed stage."""
    from app.db.schema import get_db
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " status, current_stage, media_path)"
            " VALUES (?,?,?,?,?,?,?)",
            (sample_user, "file", "https://www.instagram.com/p/Rtry8x02/",
             "Rtry8x02", "failed", "embed", "D:/nowhere/u.mp4"))
        rid = cur.lastrowid
    res = client.post(f"/api/admin/retry/{rid}")
    assert res.status_code == 200, res.text
    assert res.json()["retrying"][0] == "embed", res.text
