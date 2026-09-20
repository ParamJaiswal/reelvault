"""Phase 8C.3: the downloader had no size ceiling. validate_video rejects
files over the limit, but that runs in a later stage, after the full download
landed on disk — and a retried reel re-downloads it. The cap now applies at
fetch time, against the same limit, on every path that returns a file.
"""
import sys
from types import ModuleType

import pytest

from app.core.config import settings
from app.pipeline import media
from app.pipeline.fetch import FetchError, download_reel, verify_size

MB = 1000 * 1000


@pytest.fixture()
def vids(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    (media_dir / "video").mkdir(parents=True)
    monkeypatch.setattr(settings, "media_dir", media_dir)
    # tiny cap so the test writes 2 MB, not 500 MB
    monkeypatch.setattr(media, "MAX_VIDEO_MB", 1)
    import app.pipeline.fetch as fetch
    monkeypatch.setattr(fetch, "MAX_VIDEO_MB", 1)
    return media_dir / "video"


def _fake_yt_dlp(monkeypatch, vids, *, written_name: str,
                 reported_name: str, nbytes: int):
    """Installs a stand-in yt_dlp whose extract_info writes real bytes at
    `written_name` while prepare_filename reports `reported_name` — which may
    differ, to exercise the glob-fallback branch."""
    class Ydl:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            (vids / written_name).write_bytes(b"x" * nbytes)
            return {"id": "CAP01", "description": "", "uploader": "",
                    "title": "captured title"}

        def prepare_filename(self, info):
            return str(vids / reported_name)

    fake = ModuleType("yt_dlp")
    fake.YoutubeDL = Ydl
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)


class TestVerifySize:
    def test_under_the_limit_is_returned_untouched(self, vids):
        f = vids / "ok.mp4"
        f.write_bytes(b"x" * 1000)
        assert verify_size(f) == f
        assert f.exists()

    def test_over_the_limit_is_rejected_and_deleted(self, vids):
        f = vids / "huge.mp4"
        f.write_bytes(b"x" * (2 * MB))
        with pytest.raises(FetchError) as ei:
            verify_size(f)
        assert not f.exists(), "an oversized file must not linger on disk"
        assert "1 MB" in str(ei.value)

    def test_missing_file_is_not_masked_by_the_cap_check(self, vids):
        """stat() failing is the caller's problem to report, not ours to turn
        into a misleading size error."""
        assert verify_size(vids / "gone.mp4") == vids / "gone.mp4"

    def test_downloader_and_media_stage_share_one_limit(self):
        import app.pipeline.fetch as fetch

        assert fetch.MAX_VIDEO_MB is media.MAX_VIDEO_MB


class TestDownloadReelEnforcesCap:
    def test_oversized_direct_download_is_rejected(self, vids, monkeypatch):
        _fake_yt_dlp(monkeypatch, vids, written_name="CAP01.mp4",
                     reported_name="CAP01.mp4", nbytes=2 * MB)
        with pytest.raises(FetchError):
            download_reel("https://www.instagram.com/reel/CAP01/", "CAP01")
        assert not (vids / "CAP01.mp4").exists()

    def test_oversized_file_found_by_the_glob_fallback_is_rejected(
            self, vids, monkeypatch):
        """The branch a naive fix misses: yt-dlp reports one name, the real
        file is discovered by the `shortcode.*` glob afterwards. The written
        name must match that glob for this branch to run at all."""
        _fake_yt_dlp(monkeypatch, vids, written_name="CAP01.mp4",
                     reported_name="nomatch_xyz.mp4", nbytes=2 * MB)
        with pytest.raises(FetchError):
            download_reel("https://www.instagram.com/reel/CAP01/", "CAP01")
        assert not (vids / "CAP01.mp4").exists()

    def test_normal_download_still_returns_metadata_intact(self, vids,
                                                            monkeypatch):
        _fake_yt_dlp(monkeypatch, vids, written_name="CAP01.mp4",
                     reported_name="CAP01.mp4", nbytes=1024)
        meta = download_reel("https://www.instagram.com/reel/CAP01/", "CAP01")
        assert meta["path"] == str(vids / "CAP01.mp4")
        assert meta["title"] == "captured title"
        assert (vids / "CAP01.mp4").exists()
