"""Stage: media — ffmpeg audio (16k mono wav) + frames (1/s jpg).

One function in, one status out. Never raises.
"""
import subprocess
from pathlib import Path

from v0 import db


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr[-400:]}")


def extract_media(reel_id: int) -> str:
    reel = db.get_reel(reel_id)
    video = Path(reel["video_path"]) if reel and reel["video_path"] else None
    if not video or not video.exists():
        db.set_status(reel_id, "media_failed", error="video file missing")
        return "media_failed"

    folder = video.parent
    audio = folder / "audio.wav"
    frames = folder / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    try:
        _run(["ffmpeg", "-y", "-i", str(video), "-vn",
              "-ac", "1", "-ar", "16000", str(audio)])
        _run(["ffmpeg", "-y", "-i", str(video),
              "-vf", "fps=1", str(frames / "%04d.jpg")])
    except (RuntimeError, FileNotFoundError) as e:
        msg = "ffmpeg not found on PATH" if isinstance(e, FileNotFoundError) \
            else str(e)
        db.set_status(reel_id, "media_failed", error=msg)
        return "media_failed"

    db.set_status(reel_id, "media_ok", error=None,
                  audio_path=str(audio), frames_dir=str(frames))
    return "media_ok"
