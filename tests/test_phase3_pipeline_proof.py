"""Phase 3 — live pipeline proof (AGENTS.md): five real local videos through
the upload endpoint and durable queue, end to end.

Run explicitly (heavy: minutes, spawns real llama-server):

    RV_PHASE3=1 .venv/Scripts/python.exe -m pytest tests/test_phase3_pipeline_proof.py -v

Skipped in the normal suite so `pytest tests` stays fast.
"""
from __future__ import annotations

import importlib.util
import io
import os
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parent.parent
LLAMA_EXE = REPO / "llamacpp" / "llama-server.exe"
GGUF = REPO / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"
TESTMEDIA = REPO / "testmedia"

pytestmark = [
    pytest.mark.skipif(os.environ.get("RV_PHASE3") != "1",
                       reason="live proof — run with RV_PHASE3=1"),
    pytest.mark.skipif(not LLAMA_EXE.exists() or not GGUF.exists(),
                       reason="llama-server.exe / qwen GGUF missing"),
    pytest.mark.timeout(1500),
]

# filename -> (caption, keyword that must retrieve the reel via FTS)
CAPTIONS = {
    "job_reel.mp4": (
        "Zylker Analytics Data Analyst Intern hiring! Bangalore. Freshers "
        "apply https://zylker.example.com/careers #hiring #dataanalyst",
        "zylker"),
    "edu_reel.mp4": (
        "RAG architecture explained simply #ai #machinelearning #llm",
        "faiss"),
    "tool_reel.mp4": (
        "You don't need paid transcription. Whisper = free + local "
        "#aitools #productivity",
        "transcription"),
    "recipe_reel.mp4": (
        "Easy 3 ingredient pasta recipe. Spaghetti butter garlic parmesan "
        "#recipe #pasta",
        "pasta"),
    "fitness_reel.mp4": (
        "10 minute morning workout. Pushups squats plank, no equipment "
        "#fitness #workout",
        "plank"),
}

NEW_SCRIPTS = {
    "recipe_reel": (
        "Three ingredient pasta. Boil two hundred grams of spaghetti. "
        "Melt butter with garlic. Toss the pasta with parmesan and black "
        "pepper. Ready in ten minutes.",
        [(0.5, "3-INGREDIENT PASTA"), (3.0, "200g spaghetti"),
         (6.0, "butter + garlic"), (9.0, "parmesan + pepper")],
        CAPTIONS["recipe_reel.mp4"][0],
    ),
    "fitness_reel": (
        "Quick morning workout. Ten pushups, twenty squats, thirty second "
        "plank. Repeat three rounds. No equipment needed. Thirty days.",
        [(0.5, "MORNING WORKOUT"), (3.0, "10 pushups"), (5.0, "20 squats"),
         (7.0, "30s plank"), (9.0, "3 rounds no equipment")],
        CAPTIONS["fitness_reel.mp4"][0],
    ),
}


@pytest.fixture(scope="session")
def five_videos():
    """Materialize five distinct real reels in testmedia/ (cached)."""
    spec = importlib.util.spec_from_file_location(
        "make_test_reels", REPO / "scripts" / "make_test_reels.py")
    mtr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mtr)
    mtr.OUT = TESTMEDIA
    mtr.SCRIPTS.update(NEW_SCRIPTS)
    names = ["job_reel", "edu_reel", "tool_reel", "recipe_reel", "fitness_reel"]
    for name in names:
        mp4 = TESTMEDIA / f"{name}.mp4"
        if not mp4.exists() or mp4.stat().st_size < 10_000:
            mtr.build_reel(name)
    paths = [TESTMEDIA / f"{n}.mp4" for n in names]
    assert all(p.exists() and p.stat().st_size > 10_000 for p in paths)
    return paths


@pytest.fixture(scope="session")
def client(five_videos):
    from fastapi.testclient import TestClient

    from app.api.main import app
    from app.core.config import AUTH_TOKEN

    with TestClient(app) as c:
        yield c, AUTH_TOKEN


def _wait_for_llama(timeout_s: int = 240) -> None:
    from app.core.config import settings

    url = settings.llm_server_url.rstrip("/") + "/models"
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=3)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(3)
    pytest.fail(f"llama-server did not come up within {timeout_s}s: {last}")


def _reel_state(reel_id: int) -> dict:
    from app.db.schema import get_db

    with get_db() as db:
        reel = dict(db.execute("SELECT id, status, media_path, thumb_path,"
                               " summary, error_message"
                               " FROM reels WHERE id=?",
                               (reel_id,)).fetchone())
        jobs = [dict(r) for r in db.execute(
            "SELECT stage, status, last_error FROM jobs WHERE reel_id=?",
            (reel_id,))]
    return {"reel": reel, "jobs": jobs}


def _settled(reel_id: int) -> bool:
    s = _reel_state(reel_id)
    if not s["jobs"]:
        return False
    return all(j["status"] in ("done", "dead", "failed") for j in s["jobs"])


def _counts(reel_id: int, table: str) -> int:
    from app.db.schema import get_db

    with get_db() as db:
        return db.execute(f"SELECT COUNT(*) c FROM {table} WHERE reel_id=?",
                          (reel_id,)).fetchone()["c"]


def test_phase3_pipeline_proof(client, five_videos):
    c, token = client
    hdr = {"Authorization": f"Bearer {token}"}

    _wait_for_llama()

    # ---- ingest five real videos + one corrupt file ------------------
    ids: dict[int, str] = {}
    for path in five_videos:
        caption, _kw = CAPTIONS[path.name]
        r = c.post("/api/reels/upload",
                   files={"file": (path.name, path.open("rb"), "video/mp4")},
                   data={"caption": caption}, headers=hdr)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "queued" and not body.get("duplicate")
        ids[body["reel_id"]] = path.name

    r = c.post("/api/reels/upload",
               files={"file": ("corrupt.mp4",
                               io.BytesIO(b"this is not a video"), "video/mp4")},
               headers=hdr)
    assert r.status_code == 200
    corrupt_id = r.json()["reel_id"]

    # ---- wait for the queue to settle --------------------------------
    deadline = time.time() + 1200
    while time.time() < deadline:
        if all(_settled(rid) for rid in list(ids) + [corrupt_id]):
            break
        time.sleep(5)
    for rid in list(ids) + [corrupt_id]:
        assert _settled(rid), f"reel {rid} did not settle: {_reel_state(rid)}"

    # ---- corrupt input failed cleanly; worker stayed alive ------------
    cs = _reel_state(corrupt_id)
    assert cs["reel"]["status"] == "failed", cs
    assert cs["reel"].get("error_message") or any(
        j["last_error"] for j in cs["jobs"]), "no error recorded"
    assert any(j["status"] == "dead" for j in cs["jobs"]), cs
    # downstream stages must not have run on missing artifacts
    assert not any(j["status"] == "done" and j["stage"] in (
        "classify_extract", "embed", "finalize") for j in cs["jobs"]), cs
    assert c.get("/healthz").status_code == 200

    # ---- five good reels: end-to-end completed with artifacts ---------
    for rid, name in ids.items():
        s = _reel_state(rid)
        reel = s["reel"]
        assert reel["status"] in ("completed", "duplicate"), (name, s)
        if reel["status"] == "duplicate":
            continue
        assert Path(reel["media_path"]).exists(), name
        assert Path(reel["thumb_path"]).exists(), name
        assert _counts(rid, "frames") > 0, name
        assert _counts(rid, "ocr_results") > 0, name
        assert _counts(rid, "transcript_segments") > 0, name
        assert (reel.get("summary") or "").strip(), name
        assert _counts(rid, "facts") > 0, name

    # ---- FTS search retrieves processed content -----------------------
    for rid, name in ids.items():
        _kw = CAPTIONS[name][1]
        r = c.post("/api/search", json={"query": _kw}, headers=hdr)
        assert r.status_code == 200, r.text
        got = {row["id"] for row in r.json()["results"]}
        assert rid in got, f"{name}: keyword '{_kw}' did not retrieve reel"

    # ---- worker still alive after corrupt failures: process one more --
    import subprocess

    tiny = TESTMEDIA / "_tiny_check.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc=duration=2:size=320x240:rate=10", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-shortest", str(tiny)],
        check=True, capture_output=True)
    r = c.post("/api/reels/upload",
               files={"file": ("tiny.mp4", tiny.open("rb"), "video/mp4")},
               data={"caption": "tiny liveness check video"},
               headers=hdr)
    assert r.status_code == 200
    tiny_id = r.json()["reel_id"]
    deadline = time.time() + 600
    while time.time() < deadline and not _settled(tiny_id):
        time.sleep(5)
    assert _settled(tiny_id), "worker stopped processing after corrupt input"
    assert _reel_state(tiny_id)["reel"]["status"] == "completed"
    assert c.get("/healthz").status_code == 200
