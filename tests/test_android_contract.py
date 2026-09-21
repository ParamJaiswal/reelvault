"""Pin the HTTP contract the Android client (Repo.kt / TokenStore.kt) relies
on. No business logic — just paths, field names, auth header shape, and the
login response keys the app parses. If any of these drift, the APK breaks
silently; this file makes the break loud."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture()
def client(tmp_db):
    return TestClient(app)


@pytest.fixture()
def owner_password(tmp_db):
    """Force ensure_owner_user's credential-write path by clearing the hash
    on a fresh DB. The fresh-install path creates a password but doesn't write
    the cred file; the upgrade path does."""
    from app.core.config import settings
    from app.core import auth as A
    from app.db.schema import get_db
    A.ensure_owner_user()
    with get_db() as db:
        row = db.execute("SELECT id FROM users LIMIT 1").fetchone()
        db.execute("UPDATE users SET password_hash=NULL, password_salt=NULL"
                   " WHERE id=?", (row["id"],))
    A.ensure_owner_user()
    p = Path(settings.data_dir) / ".owner_credentials.txt"
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not obtain owner password")


@pytest.fixture()
def viewer_token(client, owner_password):
    r = client.post("/api/auth/login",
                    json={"username": "owner", "password": owner_password})
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("access_token", "refresh_token"):
        assert k in body, f"login must return {k} (TokenStore.applyLoginResponse)"
    return body["access_token"]


@pytest.fixture()
def refresh_token(client, owner_password):
    r = client.post("/api/auth/login",
                    json={"username": "owner", "password": owner_password})
    assert r.status_code == 200
    return r.json()["refresh_token"]


def test_login_returns_tokens(client, owner_password):
    r = client.post("/api/auth/login",
                    json={"username": "owner", "password": owner_password})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body and "refresh_token" in body


def test_refresh_returns_new_tokens(client, refresh_token):
    """Repo.tryRefresh calls POST /api/auth/refresh with refresh_token."""
    r = client.post("/api/auth/refresh",
                    json={"refresh_token": refresh_token})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body and "refresh_token" in body


def test_healthz_unauthenticated(client):
    r = client.get("/healthz")
    assert r.status_code == 200


def test_summaries_requires_bearer(client):
    assert client.get("/summaries").status_code in (401, 403)
    r = client.get("/summaries", headers={"Authorization": "Bearer bad"})
    assert r.status_code in (401, 403)


def test_summaries_with_bearer(client, viewer_token):
    r = client.get("/summaries?limit=50",
                   headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict) and "items" in body


def test_summaries_detail_shape(client, viewer_token):
    """DetailActivity parses facts, evidence_quote, evidence_t_s, deadline_iso."""
    r = client.get("/summaries?limit=1",
                   headers={"Authorization": f"Bearer {viewer_token}"})
    items = r.json().get("items", [])
    if not items:
        pytest.skip("no reels in test DB")
    rid = items[0]["id"]
    r = client.get(f"/summaries/{rid}",
                   headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 200
    body = r.json()
    for key in ("facts", "summary"):
        assert key in body, f"detail view must include '{key}'"


def test_ingest_upload_multipart_field_name(client, viewer_token):
    """Repo.uploadStream sends multipart field 'file'. Server must accept it."""
    fake_mp4 = io.BytesIO(b"\x00" * 64)
    r = client.post("/ingest/upload",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                    files={"file": ("clip.mp4", fake_mp4, "video/mp4")})
    assert r.status_code in (200, 202), r.text


def test_ingest_upload_rejects_missing_file_field(client, viewer_token):
    r = client.post("/ingest/upload",
                    headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 422
    assert "file" in r.text.lower()


def test_ingest_url_requires_bearer(client):
    r = client.post("/ingest/url", json={"url": "https://example.com/r/abc"})
    assert r.status_code in (401, 403)


def test_ingest_url_accepts_bearer(client, viewer_token):
    r = client.post("/ingest/url",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                    json={"url": "https://instagram.com/reel/TESTCONTRACT/"})
    assert r.status_code in (200, 202, 422), r.text
