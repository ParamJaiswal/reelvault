"""Phase 5 regression tests: a user can correct poor AI output without
editing code or rerunning the pipeline — summary/category editing,
deterministic deadline correction/removal, add/delete manual facts."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import app, require_auth


@pytest.fixture()
def client(tmp_db, sample_user):
    app.dependency_overrides[require_auth] = lambda: sample_user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture()
def reel_id(tmp_db, sample_user):
    from app.db.schema import get_db

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " summary, categories_json) VALUES (?,?,?,?,?,?)",
            (sample_user, "file", "https://www.instagram.com/reel/p5tst01/",
             "p5tst01", "AI summary text", '["Job"]'))
        return cur.lastrowid


def test_patch_summary_and_categories(client, reel_id):
    from app.db.schema import get_db

    res = client.patch(f"/api/reels/{reel_id}", json={
        "summary": "human-corrected summary",
        "categories": ["Job", " job ", "", "Tool", "Tool"]})
    assert res.status_code == 200
    with get_db() as db:
        r = dict(db.execute("SELECT summary, categories_json FROM reels"
                            " WHERE id=?", (reel_id,)).fetchone())
    assert r["summary"] == "human-corrected summary"
    assert r["categories_json"] == '["Job", "Tool"]'  # stripped + deduped


def test_patch_deadline_correction_is_deterministic(client, reel_id):
    from app.db.schema import get_db

    res = client.patch(f"/api/reels/{reel_id}",
                       json={"deadline_raw": "2027-06-01"})
    assert res.status_code == 200
    with get_db() as db:
        r = dict(db.execute("SELECT deadline_iso, reminder_iso, deadline_raw"
                            " FROM reels WHERE id=?", (reel_id,)).fetchone())
    assert r["deadline_iso"] == "2027-06-01"
    # reminder is deterministic: 5 days before, never after the deadline
    assert r["reminder_iso"] in ("2027-05-27", "2027-06-01")
    assert r["reminder_iso"] <= r["deadline_iso"]
    assert r["deadline_raw"] == "2027-06-01"


def test_patch_deadline_unparseable_rejected(client, reel_id):
    from app.db.schema import get_db

    res = client.patch(f"/api/reels/{reel_id}",
                       json={"deadline_raw": "sometime soon"})
    assert res.status_code == 422
    with get_db() as db:
        r = dict(db.execute("SELECT deadline_iso FROM reels WHERE id=?",
                            (reel_id,)).fetchone())
    assert r["deadline_iso"] is None  # nothing silently invented


def test_patch_deadline_remove(client, reel_id):
    from app.db.schema import get_db

    client.patch(f"/api/reels/{reel_id}", json={"deadline_raw": "2027-06-01"})
    res = client.patch(f"/api/reels/{reel_id}",
                       json={"deadline_remove": True})
    assert res.status_code == 200
    with get_db() as db:
        r = dict(db.execute("SELECT deadline_iso, reminder_iso, deadline_raw"
                            " FROM reels WHERE id=?", (reel_id,)).fetchone())
    assert r["deadline_iso"] is None
    assert r["reminder_iso"] is None
    assert r["deadline_raw"] is None


def test_add_manual_fact(client, reel_id):
    from app.db.schema import get_db

    res = client.post(f"/api/reels/{reel_id}/facts", json={
        "field": "company", "value": "Acme Corp",
        "quote": "Acme Corp is hiring"})
    assert res.status_code == 200
    with get_db() as db:
        f = dict(db.execute("SELECT * FROM facts WHERE reel_id=?",
                            (reel_id,)).fetchone())
    assert f["field"] == "company"
    assert f["value"] == "Acme Corp"
    assert f["user_corrected"] == 1          # human-provided, not AI output
    assert f["evidence_source"] == "metadata"
    assert f["schema_type"] == "note"
    assert f["confidence"] == 1.0


def test_add_fact_rejects_blank(client, reel_id):
    assert client.post(f"/api/reels/{reel_id}/facts",
                       json={"field": "", "value": "x"}).status_code == 422
    assert client.post(f"/api/reels/{reel_id}/facts",
                       json={"field": "f", "value": "  "}).status_code == 422


def test_delete_fact(client, reel_id):
    from app.db.schema import get_db

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO facts(reel_id, schema_type, field, value,"
            " evidence_source) VALUES (?, 'note', 'role', 'bad value',"
            " 'transcript')", (reel_id,))
        fid = cur.lastrowid
    res = client.delete(f"/api/facts/{fid}")
    assert res.status_code == 200
    with get_db() as db:
        assert db.execute("SELECT 1 FROM facts WHERE id=?",
                          (fid,)).fetchone() is None
    assert client.delete(f"/api/facts/{fid}").status_code == 404


def test_fact_endpoints_respect_ownership(client, reel_id, tmp_db):
    from app.db.schema import get_db

    with get_db() as db:
        cur = db.execute("INSERT INTO users(username, api_key_hash)"
                         " VALUES ('other','')")
        other = cur.lastrowid
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode)"
            " VALUES (?,?,?,?)",
            (other, "file", "https://www.instagram.com/reel/oth1/", "oth1"))
        foreign_reel = cur.lastrowid
        cur = db.execute(
            "INSERT INTO facts(reel_id, schema_type, field, value,"
            " evidence_source) VALUES (?, 'note', 'role', 'x',"
            " 'transcript')", (foreign_reel,))
        foreign_fact = cur.lastrowid

    # other user's reel/fact are invisible: 404, never leaked
    assert client.post(f"/api/reels/{foreign_reel}/facts",
                       json={"field": "f", "value": "v"}).status_code == 404
    assert client.delete(f"/api/facts/{foreign_fact}").status_code == 404
