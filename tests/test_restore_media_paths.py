"""Restore-verification regressions: media serving and pipeline stages must
survive a backup restore into a DIFFERENT location.

Live finding (backup-restore verification, AGENTS.md §12): reel rows store
absolute media paths at ingest time. After restoring the DB + media mirror
into a clean location, /media/video and /media/thumb 404'd because the
stored absolute path no longer exists (3 legacy rows already pointed at a
dead location). Fix: resolve_media_path() falls back to the same basename
under settings.media_dir before giving up.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.main import app, require_auth
from app.core.config import settings
from app.db.schema import get_db
from app.pipeline.media import resolve_media_path


@pytest.fixture()
def env(tmp_db, sample_user, monkeypatch, tmp_path):
    media = tmp_path / "media"
    (media / "video").mkdir(parents=True)
    (media / "frames").mkdir(parents=True)
    monkeypatch.setattr(settings, "media_dir", media)
    app.dependency_overrides[require_auth] = lambda: sample_user
    yield {"tc": TestClient(app, raise_server_exceptions=False), "media": media}
    app.dependency_overrides.clear()


def _insert_reel(media_path=None, thumb_path=None):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, status, media_path,"
            " thumb_path, title) VALUES (1,'upload','completed',?,?,"
            "'restored reel')", (media_path, thumb_path))
        return cur.lastrowid


def test_video_served_after_restore_relocation(env):
    tc, media = env["tc"], env["media"]
    (media / "video" / "upload_x.mp4").write_bytes(b"fakevideo")
    stale = str(media / "does_not_exist_here" / "upload_x.mp4")
    rid = _insert_reel(media_path=stale)
    r = tc.get(f"/media/video/{rid}")
    assert r.status_code == 200, r.text
    assert r.content == b"fakevideo"


def test_thumb_served_after_restore_relocation(env):
    tc, media = env["tc"], env["media"]
    (media / "frames" / "thumb_r1.jpg").write_bytes(b"fakethumb")
    stale = str(media / "old_install" / "thumb_r1.jpg")
    rid = _insert_reel(thumb_path=stale)
    r = tc.get(f"/media/thumb/{rid}")
    assert r.status_code == 200, r.text
    assert r.content == b"fakethumb"


def test_media_404_when_artifact_gone(env):
    tc, media = env["tc"], env["media"]
    stale = str(media / "old_install" / "lost.mp4")
    rid = _insert_reel(media_path=stale)
    assert tc.get(f"/media/video/{rid}").status_code == 404


def test_resolver_prefers_live_path_then_basename_fallback(env, tmp_path):
    media = env["media"]
    live = media / "video" / "a.mp4"
    live.write_bytes(b"x")
    assert resolve_media_path(str(live), "video") == live
    fallback_root = media / "b.mp4"
    fallback_root.write_bytes(b"x")
    assert resolve_media_path(str(tmp_path / "gone" / "b.mp4")) == fallback_root
    assert resolve_media_path("video/a.mp4", "video") == live
    assert resolve_media_path(None) is None
    assert resolve_media_path(str(tmp_path / "gone" / "c.mp4")) is None


def test_delete_purges_relocated_media(env):
    tc, media = env["tc"], env["media"]
    v = media / "video" / "upload_del.mp4"
    t = media / "frames" / "thumb_del.jpg"
    v.write_bytes(b"v")
    t.write_bytes(b"t")
    rid = _insert_reel(media_path=str(media / "old" / "upload_del.mp4"),
                       thumb_path=str(media / "old" / "thumb_del.jpg"))
    r = tc.delete(f"/api/reels/{rid}?purge_media=true")
    assert r.status_code == 200, r.text
    assert not v.exists() and not t.exists()


def test_stage_media_finds_relocated_file(env, monkeypatch):
    """stage_media must locate media via the fallback (file found: the
    failure becomes 'unprocessable' from ffprobe, NOT 'No media on disk')."""
    from app.db.queue import StageCancelled
    from app.pipeline.stages import stage_media

    tc, media = env["tc"], env["media"]
    (media / "video" / "upload_stage.mp4").write_bytes(b"not-a-real-video")
    stale = str(media / "old_install" / "upload_stage.mp4")
    rid = _insert_reel(media_path=stale)
    with pytest.raises(StageCancelled) as ei:
        stage_media(rid, {})
    assert "unprocessable" in str(ei.value).lower()
    with get_db() as db:
        row = db.execute("SELECT status FROM reels WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "failed"
