"""Media utilities: validation, audio extraction, frame sampling, phash.

All heavy lifting via ffmpeg/ffprobe (already installed on this machine).
Frame sampling is interval-based with a cap (cost control).
phash = 64-bit DCT hash computed from downscaled grayscale frame bytes
(no external image-hash dep needed).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image

from app.core.config import settings


class MediaError(Exception):
    pass


def ffprobe(path: str) -> dict:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", path,
            ],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError as e:
        raise MediaError("ffprobe not found on PATH") from e
    if out.returncode != 0:
        raise MediaError(f"ffprobe failed: {out.stderr[:300]}")
    return json.loads(out.stdout or "{}")


def validate_video(path: str) -> dict:
    info = ffprobe(path)
    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not vstreams:
        raise MediaError("No video stream found — file may be corrupt or not a video.")
    fmt = info.get("format", {})
    dur = float(fmt.get("duration") or vstreams[0].get("duration") or 0)
    if dur <= 0.2:
        raise MediaError("Video has no playable duration.")
    size_mb = Path(path).stat().st_size / 1e6
    if size_mb > 500:
        raise MediaError("File larger than 500MB limit.")
    return {
        "duration_s": dur,
        "width": int(vstreams[0].get("width") or 0),
        "height": int(vstreams[0].get("height") or 0),
        "size_mb": round(size_mb, 2),
        "has_audio": any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
    }


def extract_audio(video: str, out_wav: str) -> str:
    """16kHz mono wav — exactly what whisper wants, tiny file."""
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video,
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out_wav],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise MediaError(f"audio extraction failed: {r.stderr[:300]}")
    return out_wav


def sample_frames(video: str, reel_id: int, duration: float) -> list[dict]:
    """Interval sampling capped at max_frames_per_reel; returns frame rows."""
    outdir = settings.media_dir / "frames" / str(reel_id)
    outdir.mkdir(parents=True, exist_ok=True)
    interval = settings.frame_sample_interval_s
    n = min(int(duration // interval) + 1, settings.max_frames_per_reel)
    if n <= 0:
        n = 1
    step = max(interval, duration / n)
    frames = []
    for i in range(n):
        t = i * step
        if t >= duration:
            break
        out = outdir / f"f_{i:03d}_{t:.1f}s.jpg"
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video,
             "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(out)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            frames.append({"t_s": round(t, 2), "path": str(out)})
    return frames


def phash(img_path: str) -> str:
    """64-bit DCT-ish perceptual hash from 32x32 grayscale."""
    img = Image.open(img_path).convert("L").resize((32, 32))
    px = list(img.getdata())
    # simple 8x8 block means -> top-8x8 DCT surrogate
    blocks = []
    for by in range(8):
        for bx in range(8):
            s = 0
            for y in range(4):
                row = (by * 4 + y) * 32
                for x in range(4):
                    s += px[row + bx * 4 + x]
            blocks.append(s / 16.0)
    mean = sum(blocks) / len(blocks)
    bits = "".join("1" if b > mean else "0" for b in blocks)
    return f"{int(bits, 2):016x}"


def make_thumb(video: str, out_jpg: str, at_s: float = 0.5) -> str:
    Path(out_jpg).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{at_s:.2f}", "-i", video,
         "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "5", out_jpg],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise MediaError(f"thumb failed: {r.stderr[:200]}")
    return out_jpg
