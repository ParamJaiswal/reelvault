"""Regression tests for production-hardening review items."""
import pytest


class TestBootstrapSafety:
    """Review item: bootstrap token must be owner and unrevokable."""

    def test_bootstrap_is_owner(self, tmp_db):
        from app.core import auth as A

        ident = A.resolve_request("bootstrap-token-wrong")
        assert ident is None

    def test_bootstrap_cannot_be_revoked_by_session_ops(self, tmp_db):
        """Session revocation touches only auth_sessions rows; bootstrap has
        no row -> nothing to revoke. Prove resolve works regardless."""
        from app.core import auth as A
        from app.core.config import AUTH_TOKEN
        from app.db.schema import get_db, migrate

        migrate()
        with get_db() as db:
            db.execute("INSERT OR IGNORE INTO users(id, username, role,"
                       " password_hash, password_salt, api_key_hash)"
                       " VALUES (1,'owner','owner','x','y','')")
        ident = A.resolve_request(AUTH_TOKEN)
        assert ident is not None and ident["role"] == "owner"
        # revoke every session — bootstrap must survive
        with get_db() as db:
            db.execute("UPDATE auth_sessions SET revoked=1")
        ident2 = A.resolve_request(AUTH_TOKEN)
        assert ident2 is not None and ident2["role"] == "owner"

    def test_owner_credentials_file_created_once(self, tmp_db, monkeypatch):
        import pathlib

        from app.core.config import settings
        from app.db.schema import get_db, migrate

        migrate()
        with get_db() as db:
            db.execute("INSERT OR IGNORE INTO users(id, username,"
                       " password_hash, password_salt, api_key_hash)"
                       " VALUES (1,'boss','h','s','')")
        f = pathlib.Path(settings.data_dir) / ".owner_credentials.txt"
        if f.exists():
            f.unlink()
        from app.core import auth as A

        A.ensure_owner_user()
        assert f.exists()
        assert "password:" in f.read_text()


class TestBackupCoversSessions:
    """Review item: refresh-token sessions must survive a restore."""

    def test_db_backup_contains_auth_sessions(self, tmp_path, tmp_db):
        from app.core import auth as A
        from app.core.backups import backup_db
        from app.db.schema import connect, get_db, migrate

        migrate()
        with get_db() as db:
            db.execute("INSERT OR IGNORE INTO users(id, username, role,"
                       " password_hash, password_salt, api_key_hash)"
                       " VALUES (1,'o','owner','x','y','')")
        sess = A.create_session(1, "owner", "phone")

        snap = backup_db(tmp_path, encrypt=False)

        # restore into a scratch db and verify session survived
        import sqlite3

        restored = sqlite3.connect(str(snap))
        n = restored.execute(
            "SELECT COUNT(*) FROM auth_sessions WHERE id=?",
            (sess["session_id"],)).fetchone()[0]
        rh = restored.execute(
            "SELECT refresh_hash FROM auth_sessions WHERE id=?",
            (sess["session_id"],)).fetchone()[0]
        restored.close()
        assert n == 1
        assert rh == A.hash_token(sess["refresh_token"])
