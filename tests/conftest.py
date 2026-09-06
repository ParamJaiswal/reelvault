"""Shared fixtures: isolated DB per test session."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="rv_test_")
os.environ.setdefault("RV_DB_PATH", str(Path(_TMP) / "test.db"))
os.environ.setdefault("RV_DATA_DIR", _TMP)


@pytest.fixture()
def tmp_db(monkeypatch):
    """Fresh DB per test."""
    from app.core.config import settings
    from app.db.schema import migrate

    import tempfile as tf
    d = tf.mkdtemp(prefix="rv_case_")
    settings.db_path = Path(d) / "case.db"
    settings.data_dir = Path(d)
    migrate()
    yield settings.db_path


@pytest.fixture()
def sample_user(tmp_db):
    from app.db.schema import get_db

    with get_db() as db:
        cur = db.execute("INSERT INTO users(username, display_name, api_key_hash)"
                         " VALUES ('t','Tester','')")
        return cur.lastrowid
