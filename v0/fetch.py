"""Stage: fetch — yt-dlp download to video.mp4. Fail = fetch_failed.

Never raises; a bad URL is a status, not a crash.
"""
import subprocess
import sys
from pathlib import Path

from v0 import db
from v0.config import MEDIA_DIR


def fetch_url(reel_id: int, url: str) -> str:
    """Download url. Returns 'fetched' or 'fetch_failed'."""
    folder = MEDIA_DIR / str(reel_id)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "video.mp4"
    cmd = [sys.executable, "-m", "yt_dlp", "-f", "mp4/best",
           "--no-playlist", "--quiet", "--socket-timeout", "10",
           "-o", str(out), url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        db.set_status(reel_id, "fetch_failed", error="download timed out")
        return "fetch_failed"
    if p.returncode != 0 or not out.exists():
        err = (p.stderr or p.stdout or "yt-dlp failed")[-400:]
        db.set_status(reel_id, "fetch_failed", error=err)
        return "fetch_failed"

    db.set_status(reel_id, "queued", error=None, video_path=str(out))
    return "fetched"
