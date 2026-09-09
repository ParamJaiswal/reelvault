"""Pipeline reliability tests: B5 Whisper hallucination filter, B6 phash
frame dedup + persistence, C1 per-stage timing, C2 WAL checkpointing, C3
worker stop event. Deterministic fakes only — no ffmpeg, no GPU, no LLM."""
import io
import json
import random
import threading
from types import SimpleNamespace
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.db import queue as queue_mod
from app.db.queue import Queue
from app.db.schema import get_db
from app.pipeline import media, stages


class FakeTranscriber:
    name = "fake-whisper"

    def __init__(self, result):
        self.result = result

    def transcribe(self, wav_path, reel_id=None):
        return self.result


def mk_reel(user_id, media_path=None, shortcode="TSTaaa111"):
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO users(id, username, api_key_hash)"
                   " VALUES (1,'t','')")
        uid = user_id or 1
        if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            cur = db.execute("INSERT INTO users(username, api_key_hash)"
                             " VALUES ('t2','')")
            uid = cur.lastrowid
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " media_path) VALUES (?,?,?,?,?)",
            (uid, "file", f"https://www.instagram.com/reel/{shortcode}/",
             shortcode, media_path))
        return cur.lastrowid


def _noise_jpg_bytes(rng, size=64):
    img = Image.new("L", (size, size))
    img.putdata([rng.randrange(256) for _ in range(size * size)])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# --------------------------------------------------------------- B5
def test_transcribe_drops_hallucinated_segments(tmp_db, monkeypatch,
                                                sample_user):
    monkeypatch.setattr(settings, "media_dir", tmp_db.parent / "media")
    rid = mk_reel(sample_user)
    wav = settings.media_dir / "audio" / f"r{rid}.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"RIFF")  # existence only; transcriber is faked

    segs = [
        # classic hallucination: probable silence + very low confidence
        {"start": 0.0, "end": 1.0, "text": "Thanks for watching!",
         "avg_logprob": -2.5, "no_speech_prob": 0.95},
        {"start": 1.0, "end": 2.0, "text": "python is great",
         "avg_logprob": -0.3, "no_speech_prob": 0.05},
        # quiet but not silent enough to drop (no_speech_prob <= 0.7)
        {"start": 2.0, "end": 3.0, "text": "quiet but real",
         "avg_logprob": -1.5, "no_speech_prob": 0.5},
        # confident non-speech score alone must NOT drop a segment (AND rule)
        {"start": 3.0, "end": 4.0, "text": "confident garbage",
         "avg_logprob": -0.2, "no_speech_prob": 0.9},
        # exactly at both thresholds: strict comparisons keep it
        {"start": 4.0, "end": 5.0, "text": "at the boundary",
         "avg_logprob": -1.0, "no_speech_prob": 0.7},
        # provider omitted probability fields: kept, no invented thresholds
        {"start": 5.0, "end": 6.0, "text": "no prob fields"},
    ]
    monkeypatch.setattr(
        stages.providers, "get_transcriber",
        lambda: FakeTranscriber({"language": "en", "segments": segs}))

    stages.stage_transcribe(rid, {})

    with get_db() as db:
        texts = [r["text"] for r in db.execute(
            "SELECT text FROM transcript_segments WHERE reel_id=?"
            " ORDER BY start_s", (rid,))]
        row = db.execute(
            "SELECT message, data_json FROM processing_events"
            " WHERE reel_id=? AND stage='transcribe' ORDER BY id DESC",
            (rid,)).fetchone()
    assert texts == ["python is great", "quiet but real", "confident garbage",
                     "at the boundary", "no prob fields"]
    assert "1 hallucinated dropped" in row["message"]
    assert json.loads(row["data_json"])["dropped_hallucinated"] == 1


def test_transcribe_keeps_all_when_no_hallucinations(tmp_db, monkeypatch,
                                                     sample_user):
    monkeypatch.setattr(settings, "media_dir", tmp_db.parent / "media")
    rid = mk_reel(sample_user)
    wav = settings.media_dir / "audio" / f"r{rid}.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"RIFF")
    segs = [{"start": 0.0, "end": 1.0, "text": "real speech",
             "avg_logprob": -0.4, "no_speech_prob": 0.1}]
    monkeypatch.setattr(
        stages.providers, "get_transcriber",
        lambda: FakeTranscriber({"language": "en", "segments": segs}))
    stages.stage_transcribe(rid, {})
    with get_db() as db:
        n = db.execute("SELECT COUNT(*) c FROM transcript_segments"
                       " WHERE reel_id=?", (rid,)).fetchone()["c"]
        row = db.execute(
            "SELECT message FROM processing_events WHERE reel_id=?"
            " AND stage='transcribe' ORDER BY id DESC", (rid,)).fetchone()
    assert n == 1
    assert "hallucinated" not in row["message"]


# --------------------------------------------------------------- B6
def test_hamming_distance_hex():
    assert media.hamming_distance("0" * 16, "0" * 16) == 0
    assert media.hamming_distance("0" * 16, "8" + "0" * 15) == 1
    assert media.hamming_distance("0" * 16, "f" * 16) == 64
    # unparseable/absent hashes: maximally different, never triggers a drop
    assert media.hamming_distance("zz", "00") == 64
    assert media.hamming_distance(None, "00") == 64


def test_sample_frames_dedups_near_identical_frames(tmp_db, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_db.parent / "media")
    monkeypatch.setattr(settings, "frame_sample_interval_s", 1.0)
    monkeypatch.setattr(settings, "max_frames_per_reel", 10)
    rng = random.Random(42)
    img_a = _noise_jpg_bytes(rng)
    img_b = _noise_jpg_bytes(rng)

    def fake_ffmpeg(cmd, **kw):
        out = Path(cmd[-1])
        i = int(out.name.split("_")[1])  # f_001_1.0s.jpg -> 1
        out.write_bytes(img_a if i <= 1 else img_b)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(media.subprocess, "run", fake_ffmpeg)

    frames = media.sample_frames("ignored.mp4", 4242, 3.0)

    # frame 1 duplicates frame 0 (identical phash) and must never reach OCR
    assert [Path(f["path"]).name for f in frames] == \
        ["f_000_0.0s.jpg", "f_002_2.0s.jpg"]
    hashes = [f["phash"] for f in frames]
    assert all(h and len(h) == 16 for h in hashes)
    assert media.hamming_distance(hashes[0], hashes[1]) \
        >= media.PHASH_MIN_HAMMING_DISTANCE
    assert not (settings.media_dir / "frames" / "4242"
                / "f_001_1.0s.jpg").exists()


def test_stage_media_persists_frame_phash(tmp_db, monkeypatch, sample_user):
    monkeypatch.setattr(settings, "media_dir", tmp_db.parent / "media")
    for sub in ("video", "audio", "frames"):
        (settings.media_dir / sub).mkdir(parents=True, exist_ok=True)
    vid = settings.media_dir / "video" / "v1.mp4"
    vid.write_bytes(b"fake")
    rid = mk_reel(sample_user, media_path=str(vid))

    jpg = settings.media_dir / "frames" / "sample.jpg"
    Image.new("RGB", (8, 8), "white").save(jpg, "JPEG")

    monkeypatch.setattr(stages, "validate_video",
                        lambda p: {"duration_s": 2.0, "width": 640,
                                   "height": 360, "size_mb": 1.0,
                                   "has_audio": True})

    def fake_extract(video, out_wav):
        Path(out_wav).write_bytes(b"RIFF")
        return out_wav

    def fake_thumb(video, out_jpg, at_s=0.5):
        Path(out_jpg).write_bytes(jpg.read_bytes())
        return out_jpg

    monkeypatch.setattr(stages, "extract_audio", fake_extract)
    monkeypatch.setattr(stages, "make_thumb", fake_thumb)
    monkeypatch.setattr(stages, "sample_frames", lambda v, r, d: [
        {"t_s": 0.0, "path": str(jpg), "phash": "ab" * 8},
        {"t_s": 1.0, "path": str(jpg)},  # provider without phash -> NULL
    ])

    stages.stage_media(rid, {})

    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT t_s, phash FROM frames WHERE reel_id=? ORDER BY t_s",
            (rid,))]
    assert [(r["t_s"], r["phash"]) for r in rows] == \
        [(0.0, "ab" * 8), (1.0, None)]


# --------------------------------------------------------------- C1
def test_run_pending_records_stage_timing(tmp_db, sample_user):
    q = Queue()
    rid = mk_reel(sample_user)
    q.register("ocr", lambda reel_id, payload: None)
    q.enqueue(rid, ["ocr"])
    assert q.run_pending("w0") == 1
    with get_db() as db:
        row = db.execute(
            "SELECT message, data_json FROM processing_events"
            " WHERE reel_id=? AND stage='ocr' ORDER BY id DESC",
            (rid,)).fetchone()
    assert row["message"].startswith("stage completed in ")
    assert json.loads(row["data_json"])["duration_s"] >= 0

    # a failed stage must not emit a 'stage completed' timing event
    def boom(reel_id, payload):
        raise RuntimeError("boom")

    q.register("embed", boom)
    q.enqueue(rid, ["embed"])
    q.run_pending("w0")
    with get_db() as db:
        n = db.execute(
            "SELECT COUNT(*) c FROM processing_events"
            " WHERE reel_id=? AND stage='embed'"
            " AND message LIKE 'stage completed%'",
            (rid,)).fetchone()["c"]
    assert n == 0


# --------------------------------------------------------------- C2
def test_wal_checkpoint_runs_after_threshold(tmp_db, monkeypatch,
                                             sample_user):
    monkeypatch.setattr(queue_mod, "WAL_CHECKPOINT_EVERY_JOBS", 2)
    q = Queue()
    checkpoints = []
    monkeypatch.setattr(q, "_wal_checkpoint", lambda: checkpoints.append(1))
    rid = mk_reel(sample_user)
    q.register("ocr", lambda reel_id, payload: None)
    q.register("embed", lambda reel_id, payload: None)

    q.enqueue(rid, ["ocr", "embed"])
    q.run_pending("w0")              # 2 completed -> checkpoint
    assert checkpoints == [1]
    q.enqueue(rid, ["ocr"])
    q.run_pending("w0")              # 1 completed -> below threshold
    assert checkpoints == [1]
    q.enqueue(rid, ["embed"])
    q.run_pending("w0")              # 2 more -> checkpoint again
    assert checkpoints == [1, 1]

    # the real path executes PRAGMA wal_checkpoint(TRUNCATE) without error
    q2 = Queue()
    q2._completed_since_checkpoint = queue_mod.WAL_CHECKPOINT_EVERY_JOBS
    q2._maybe_wal_checkpoint()


# --------------------------------------------------------------- C3
def test_run_pending_stop_event_prevents_claiming(tmp_db, sample_user):
    q = Queue()
    rid = mk_reel(sample_user)
    q.register("media", lambda reel_id, payload: None)
    q.register("ocr", lambda reel_id, payload: None)
    q.enqueue(rid, ["media", "ocr"])

    stop = threading.Event()
    stop.set()
    assert q.run_pending("w0", stop_event=stop) == 0
    assert q.stats().get("queued") == 2


def test_run_pending_stop_event_finishes_current_job_first(tmp_db,
                                                           sample_user):
    q = Queue()
    rid = mk_reel(sample_user)
    stop = threading.Event()

    def media_handler(reel_id, payload):
        stop.set()  # shutdown requested while a job is in flight

    q.register("media", media_handler)
    q.register("ocr", lambda reel_id, payload: None)
    q.enqueue(rid, ["media", "ocr"])

    # in-flight job completes, then no NEW job is claimed
    assert q.run_pending("w0", stop_event=stop) == 1
    assert q.stats().get("done") == 1
    assert q.stats().get("queued") == 1

    stop.clear()
    assert q.run_pending("w0", stop_event=stop) == 1
    assert q.stats().get("done") == 2
