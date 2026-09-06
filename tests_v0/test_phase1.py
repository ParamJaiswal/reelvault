"""Phase 1 done check: upload → wav + frames exist; bad URL → fetch_failed."""
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from v0 import db, main

client = TestClient(main.app)


def _make_mp4(path: Path, seconds: int = 2) -> None:
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-shortest", str(path)],
        check=True, capture_output=True)


def test_db_roundtrip():
    rid = db.create_reel("upload")
    db.set_status(rid, "media_ok", audio_path="x")
    reel = db.get_reel(rid)
    assert reel["status"] == "media_ok"
    assert reel["audio_path"] == "x"


def test_upload_produces_wav_and_frames(tmp_path):
    mp4 = tmp_path / "in.mp4"
    _make_mp4(mp4)
    with mp4.open("rb") as f:
        resp = client.post("/ingest/upload",
                           files={"file": ("test.mp4", f, "video/mp4")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "media_ok"

    reel = db.get_reel(body["id"])
    assert Path(reel["video_path"]).exists()
    assert Path(reel["audio_path"]).exists()
    frames = list(Path(reel["frames_dir"]).glob("*.jpg"))
    assert len(frames) >= 1


def test_bad_url_returns_fetch_failed():
    resp = client.post("/ingest/url",
                       json={"url": "https://invalid.invalid/reel"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fetch_failed"
    reel = db.get_reel(body["id"])
    assert reel["status"] == "fetch_failed"
    assert reel["error"]
