"""Media utilities: validation, audio extraction, frame sampling, phash.

All heavy lifting via ffmpeg/ffprobe (already installed on this machine).
Frame sampling is interval-based with a cap (cost control).
phash = 64-bit DCT hash computed from downscaled grayscale frame bytes
(no external image-hash dep needed).
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.db.queue import PermanentJobError

log = logging.getLogger("rv.media")

# Near-duplicate frame suppression (B6): a sampled frame whose 64-bit phash
# is closer than this Hamming distance to the previous KEPT frame never
# reaches OCR — static overlays would otherwise be OCR'd once per sample.
PHASH_MIN_HAMMING_DISTANCE = 5

# Storage ceiling for one imported video, enforced by validate_video and by
# the downloader. Kept in one place so a fetched file and an uploaded file
# face the same rule.
MAX_VIDEO_MB = 500


class MediaError(Exception):
    pass


class PermanentMediaError(PermanentJobError, MediaError):
    """Deterministic media failure (unreadable/corrupt/oversized/missing).
    Subclasses MediaError so existing ``except MediaError`` handlers keep
    working, and PermanentJobError so the queue skips the retry ladder."""


def resolve_media_path(stored: str | None, sub: str | None = None) -> Path | None:
    """Resolve a DB-stored media path against the CURRENT settings.

    Reel rows store absolute paths at ingest time. After a backup restore
    (or a media-dir move) those absolutes are stale, so fall back to the
    same basename under settings.media_dir before giving up. Returns a
    live Path or None when the artifact is genuinely gone.
    """
    if not stored:
        return None
    p = Path(stored)
    if not p.is_absolute():
        # relative rows resolve against the configured media dir, with CWD
        # as a compatibility fallback (older rows/tests stored CWD-relative
        # paths like media/video/x.mp4)
        p = settings.media_dir / p
        if not p.exists() and (Path.cwd() / stored).exists():
            return Path.cwd() / stored
    if p.exists():
        return p
    name = Path(stored).name
    for cand in ((settings.media_dir / name),
                 (settings.media_dir / sub / name) if sub else None):
        if cand is not None and cand.exists():
            return cand
    return None


def ffprobe(path: str) -> dict:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", path,
            ],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        # A file ffprobe cannot parse in 60s will not heal on retry
        # (observed: garbage upload burned 3x60s in the retry ladder).
        raise PermanentMediaError(
            "ffprobe timed out after 60s — file unreadable") from e
    except FileNotFoundError as e:
        raise MediaError("ffprobe not found on PATH") from e
    if out.returncode != 0:
        raise PermanentMediaError(f"ffprobe failed: {out.stderr[:300]}")
    return json.loads(out.stdout or "{}")


def validate_video(path: str) -> dict:
    info = ffprobe(path)
    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not vstreams:
        raise PermanentMediaError(
            "No video stream found — file may be corrupt or not a video.")
    fmt = info.get("format", {})
    dur = float(fmt.get("duration") or vstreams[0].get("duration") or 0)
    if dur <= 0.2:
        raise PermanentMediaError("Video has no playable duration.")
    size_mb = Path(path).stat().st_size / 1e6
    if size_mb > MAX_VIDEO_MB:
        raise PermanentMediaError(
            f"File larger than {MAX_VIDEO_MB}MB limit.")
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


def hamming_distance(h1: str, h2: str) -> int:
    """Hamming distance between two hex phash strings (0..64).

    Unparseable hashes return 64 (maximally different) so a corrupt hash
    can never cause a frame to be dropped."""
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def sample_frames(video: str, reel_id: int, duration: float) -> list[dict]:
    """Interval sampling capped at max_frames_per_reel; returns frame rows.

    Frames near-identical (phash Hamming < PHASH_MIN_HAMMING_DISTANCE) to
    the previous kept frame are deleted before returning, so OCR only sees
    visually distinct frames. The first frame is always kept; a frame whose
    hash cannot be computed is kept rather than silently dropped."""
    outdir = settings.media_dir / "frames" / str(reel_id)
    outdir.mkdir(parents=True, exist_ok=True)
    interval = settings.frame_sample_interval_s
    n = min(int(duration // interval) + 1, settings.max_frames_per_reel)
    if n <= 0:
        n = 1
    step = max(interval, duration / n)
    frames = []
    prev_hash: str | None = None
    dropped = 0
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
            try:
                h = phash(str(out))
            except Exception:  # noqa: BLE001 - unreadable frame must survive
                h = None
            if (h is not None and prev_hash is not None
                    and hamming_distance(h, prev_hash)
                    < PHASH_MIN_HAMMING_DISTANCE):
                dropped += 1
                out.unlink(missing_ok=True)
                continue
            if h is not None:
                prev_hash = h
            frames.append({"t_s": round(t, 2), "path": str(out), "phash": h})
    if dropped:
        log.info("sample_frames reel=%s: dropped %d near-duplicate frame(s)",
                 reel_id, dropped)
    return frames


def phash(img_path: str) -> str:
    """64-bit DCT-ish perceptual hash from 32x32 grayscale."""
    img = Image.open(img_path).convert("L").resize((32, 32))
    px = list(img.get_flattened_data())
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
