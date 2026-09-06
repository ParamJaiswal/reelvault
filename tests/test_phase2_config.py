"""Phase 2 regression tests: portable path defaults, restricted CORS,
startup path validation, stale owner-credentials warning."""
import os

import pytest


def test_default_paths_are_repo_relative(monkeypatch):
    """Old bug: defaults pointed at D:/reelvault — breaks any other machine."""
    from app.core.config import APP_ROOT, Settings

    for key in [k for k in os.environ if k.startswith("RV_")]:
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.models_dir == APP_ROOT / "models"
    assert s.media_dir == APP_ROOT / "media"
    assert s.backup_dir == str(APP_ROOT / "backups")


def test_cors_default_not_wildcard(monkeypatch):
    from app.core.config import Settings

    for key in [k for k in os.environ if k.startswith("RV_")]:
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert "*" not in s.cors_origins
    origins = [o.strip() for o in s.cors_origins.split(",")]
    assert "http://127.0.0.1:8756" in origins


def test_validate_startup_paths_raises_on_unwritable_db_dir(tmp_path,
                                                            monkeypatch):
    from app.core import config as C

    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory")
    monkeypatch.setattr(C.settings, "db_path", blocker / "sub" / "test.db")
    with pytest.raises(RuntimeError, match="Startup path validation failed"):
        C.validate_startup_paths()


def test_validate_startup_paths_warns_on_missing_models(tmp_path, monkeypatch,
                                                        caplog):
    from app.core import config as C

    d = tmp_path / "ok"
    monkeypatch.setattr(C.settings, "data_dir", d)
    monkeypatch.setattr(C.settings, "media_dir", d)
    monkeypatch.setattr(C.settings, "db_path", d / "test.db")
    monkeypatch.setattr(C.settings, "models_dir", tmp_path / "nope")
    with caplog.at_level("WARNING", logger="rv.config"):
        C.validate_startup_paths()  # must not raise
    assert "models dir missing" in caplog.text


def test_owner_credentials_warning_when_file_present(tmp_path, monkeypatch,
                                                     caplog):
    from app.core import config as C

    monkeypatch.setattr(C.settings, "data_dir", tmp_path)
    (tmp_path / ".owner_credentials.txt").write_text("seed")
    with caplog.at_level("WARNING", logger="rv.config"):
        C.warn_if_owner_credentials_stale()
    assert "owner credentials" in caplog.text.lower()


def test_no_owner_credentials_warning_when_absent(tmp_path, monkeypatch,
                                                  caplog):
    from app.core import config as C

    monkeypatch.setattr(C.settings, "data_dir", tmp_path)
    with caplog.at_level("WARNING", logger="rv.config"):
        C.warn_if_owner_credentials_stale()
    assert "owner credentials" not in caplog.text.lower()
