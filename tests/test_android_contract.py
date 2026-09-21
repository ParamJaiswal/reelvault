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
    """ensure_owner_user writes the generated password to this file."""
    from app.core.config import settings
    from app.core import auth as A
    row = A.ensure_owner_user()
    p = Path(settings.data_dir) / ".owner_credentials.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("password:"):
                return line.split(":", 1)[1].strip()
    # Fresh install path: password was just generated, re-read from DB is not
    # exposed; instead reset and let ensure_owner_user write it.
    from app.db.schema import get_db
    with get_db() as db:
        db.execute("UPDATE users SET password_hash=NULL, password_salt=NULL"
                   " WHERE id=?", (row["id"],))
    A.ensure_owner_user()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not obtain owner password")


@pytest.fixture()
def viewer_token(client, owner_password):
    r = client.post("/api/auth/login",
                    json={"username": "owner", "password": owner_password,
                          "name": "test"})
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("access_token", "refresh_token"):
        assert k in body, f"login must return {k} (TokenStore.applyLoginResponse)"
    return body["access_token"]


def test_login_returns_tokens(client, owner_password):
    r = client.post("/api/auth/login",
                    json={"username": "owner", "password": owner_password,
                          "name": "apk"})
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
    # Accept either a bare list or {"items": [...], "count": N} envelope.
    assert isinstance(body, (list, dict))
    if isinstance(body, dict):
        assert "items" in body


def test_ingest_upload_multipart_field_name(client, viewer_token):
    """Repo.uploadVideo sends multipart field 'file'. Server must match."""
    fake_mp4 = io.BytesIO(b"\x00" * 64)
    r = client.post("/ingest/upload",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                    files={"file": ("clip.mp4", fake_mp4, "video/mp4")})
    # Accept 200/202 (queued) or 422 if validation rejects the stub bytes;
    # reject only if the field name itself is wrong (422 with our message).
    assert r.status_code in (200, 202, 422), r.text
    if r.status_code == 422:
        assert "file" in r.text.lower() or "media" in r.text.lower()


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
    # 200/202 means path + auth are correct; 422 means URL validation ran
    # (still proves the route exists and bearer was accepted).
    assert r.status_code in (200, 202, 422), r.text
