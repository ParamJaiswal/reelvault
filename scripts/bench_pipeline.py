"""Pipeline stage benchmark — measures the heavy stages (media, transcribe,
ocr) on a synthetic 2-second reel, no LLM needed.

Usage:
    .venv/Scripts/python.exe scripts/bench_pipeline.py

Records one row per stage in seconds; compare against docs/PERF.md notes
or past outputs to catch regressions before optimizing. AGENTS.md rule:
optimize speed only after measuring real pipeline time.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.pipeline import media  # noqa: E402


def make_test_clip(dest: Path, seconds: int = 2) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=640x360:rate=10",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-shortest", "-c:v", "libx264", str(dest)],
        check=True, capture_output=True)


def bench() -> dict[str, float]:
    t = {}
    tmp = Path(tempfile.mkdtemp(prefix="rv_bench_"))
    clip = tmp / "bench.mp4"
    make_test_clip(clip)

    t0 = time.perf_counter()
    info = media.validate_video(str(clip))
    t["media.validate"] = time.perf_counter() - t0

    wav = tmp / "bench.wav"
    t0 = time.perf_counter()
    media.extract_audio(str(clip), str(wav))
    t["media.extract_audio"] = time.perf_counter() - t0

    thumb = tmp / "thumb.jpg"
    t0 = time.perf_counter()
    media.make_thumb(str(clip), str(thumb))
    t["media.make_thumb"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    frames = media.sample_frames(str(clip), 999999, info["duration_s"])
    t["media.sample_frames"] = time.perf_counter() - t0

    from app.ai import providers
    tr = providers.get_transcriber()
    t0 = time.perf_counter()
    res = tr.transcribe(str(wav))
    t[f"transcribe ({tr.name})"] = time.perf_counter() - t0

    ocr = providers.get_ocr()
    if ocr is not None:
        t0 = time.perf_counter()
        for fr in frames[:5]:
            ocr.read_image(fr["path"])
        t[f"ocr x{min(5, len(frames))}"] = time.perf_counter() - t0

    print(f"\nclip: {info['duration_s']}s {info['width']}x{info['height']} "
          f"audio={info['has_audio']} frames={len(frames)}")
    total = 0.0
    for k, v in t.items():
        print(f"  {k:34s} {v:7.2f}s")
        if "transcribe" not in k or True:
            total += v
    print(f"  {'TOTAL (heavy stages)':34s} {total:7.2f}s")
    return t


if __name__ == "__main__":
    bench()
