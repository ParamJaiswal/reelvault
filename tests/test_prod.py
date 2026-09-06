"""Tests for auth (sessions, rotation, reuse-detection), backups, deadlines."""
import pytest


@pytest.fixture()
def owner(tmp_db):
    from app.db.schema import get_db, migrate

    migrate()   # ensure v4 columns exist
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO users(id, username, role,"
                   " password_hash, password_salt, api_key_hash)"
                   " VALUES (1,'owner','owner','x','y','')")
    return 1


class TestAuth:
    def test_password_hash_roundtrip(self):
        from app.core.auth import hash_password, verify_password

        h, s = hash_password("secret-pass-1")
        assert verify_password("secret-pass-1", h, s)
        assert not verify_password("wrong", h, s)

    def test_session_create_rotate_revoke(self, tmp_db, owner):
        from app.core import auth as A

        sess = A.create_session(owner, "owner", "test-device")
        ident = A.resolve_request(sess["access_token"])
        assert ident and ident["role"] == "owner"
        # rotate
        r1 = A.rotate_session(sess["refresh_token"])
        assert r1 and r1["refresh_token"] != sess["refresh_token"]
        # OLD refresh reused -> family revoked
        r2 = A.rotate_session(sess["refresh_token"])
        assert r2 is None
        # access token now dead too (session revoked)
        assert A.resolve_request(r1["access_token"]) is None

    def test_revoke_session_kills_access(self, tmp_db, owner):
        from app.core import auth as A

        sess = A.create_session(owner, "admin", "phone")
        assert A.resolve_request(sess["access_token"]) is not None
        A.revoke_session(sess["session_id"], owner, "owner")
        assert A.resolve_request(sess["access_token"]) is None

    def test_garbage_tokens(self, tmp_db):
        from app.core import auth as A

        assert A.resolve_request("") is None
        assert A.resolve_request("not-a-token") is None
        assert A.decode_access_jwt("a.b.c") is None

    def test_user_creation_roles(self, tmp_db, owner):
        from app.core import auth as A

        u = A.create_user("friend", "longenough1", "viewer")
        assert u and u["role"] == "viewer"
        assert A.create_user("x", "short", "viewer") is None      # pw short
        assert A.create_user("boss", "longenough1", "owner") is None  # no 2nd owner


class TestBackups:
    def test_db_backup_plaintext_and_encrypted(self, tmp_path, monkeypatch):
        from app.core import backups as B
        from app.core.config import settings

        settings.backup_passphrase = "test-pass-123"
        p_plain = B.backup_db(tmp_path, encrypt=False)
        assert p_plain.exists() and p_plain.stat().st_size > 0
        p_enc = B.backup_db(tmp_path, encrypt=True)
        raw = p_enc.read_bytes()
        assert b"SQLite" not in raw[:16]          # actually encrypted
        # decrypt roundtrip
        from cryptography.fernet import Fernet

        f = Fernet(B._key_from_passphrase("test-pass-123"))
        assert b"SQLite" in f.decrypt(raw)[:16]

    def test_media_mirror_incremental(self, tmp_path):
        from app.core import backups as B
        from app.core.config import settings

        media = tmp_path / "media"
        media.mkdir(parents=True)
        (media / "a.txt").write_text("v1")
        settings.media_dir = media
        c, _ = B.backup_media(tmp_path / "backups")
        assert c == 1                             # one file copied
        # unchanged second run
        c2, s2 = B.backup_media(tmp_path / "backups")
        assert c2 == 0 and s2 >= 1


class TestPushKeys:
    def test_vapid_generation_or_graceful(self, tmp_db, monkeypatch):
        from app.core.config import settings
        from app.core import push as P

        monkeypatch.setattr(settings.__class__, "data_dir",
                            property(lambda self: __import__("pathlib").Path(
                                self.data_dir)), raising=False)
        key = P.vapid_public_key()   # may be "" if py_vapid missing
        if key:
            assert len(key) > 40
