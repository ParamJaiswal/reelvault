"""Bulk-ingest soak: push N distinct synthetic reels through the live API
concurrently and verify the queue drains cleanly (no worker death, no
database locks, all reels terminal, per-reel timing).

Usage:
    .venv/Scripts/python.exe -X utf8 scripts/soak_bulk_ingest.py [N] [--port 8756]

N defaults to 20. Clips are ffmpeg testsrc+sine with unique drawtext labels
and durations so content-hash dedupe never triggers. Results print as a
table; exit code 1 if any reel does not reach a terminal state or the
worker dies.
"""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

N = 20
PORT = 8756
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if args:
    N = int(args[0])
for a in sys.argv[1:]:
    if a.startswith("--port="):
        PORT = int(a.split("=")[1])

BASE = f"http://127.0.0.1:{PORT}"


def call(path: str, body=None, tok=None, method=None, timeout=120):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.urlopen(urllib.request.Request(
        BASE + path, data, h, method=method), timeout=timeout)
    return json.loads(r.read())


def login() -> str:
    from pathlib import Path
    creds = Path("data/.owner_credentials.txt").read_text()
    user = pw = None
    for line in creds.splitlines():
        if "user" in line.lower() and ":" in line:
            user = line.split(":", 1)[1].strip()
        if "pass" in line.lower() and ":" in line:
            pw = line.split(":", 1)[1].strip()
    return call("/api/auth/login", {"username": user, "password": pw})[
        "access_token"]


def make_clip(dest: Path, i: int) -> None:
    dur = 2 + (i % 5)  # 2..6s, distinct per reel
    # NOTE: no drawtext — it access-violates (0xC0000005) under python
    # subprocess on this fontconfig-less setup. Uniqueness comes from
    # duration + tone frequency (bytes differ -> content_hash differs).
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i",
         f"testsrc=duration={dur}:size=480x854:rate=10",
         "-f", "lavfi", "-i",
         f"sine=frequency={300 + i * 40}:duration={dur}",
         "-vf", f"hue=h={i * 36}:s=1+{i % 4}",
         "-shortest", "-c:v", "libx264", "-c:a", "aac", str(dest)],
        check=True, capture_output=True)


def main() -> int:
    tok = login()
    print(f"soak: {N} reels, port {PORT}")

    tmp = Path(tempfile.mkdtemp(prefix="rv_soak_"))
    clips = []
    for i in range(N):
        c = tmp / f"soak_{i:02d}.mp4"
        make_clip(c, i)
        clips.append(c)
    print(f"generated {len(clips)} clips in {tmp}")

    import uuid
    boundary = uuid.uuid4().hex

    def upload(path: Path) -> dict:
        body = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; "
            f"filename=\"{path.name}\"\r\n"
            f"Content-Type: video/mp4\r\n\r\n"
        ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            BASE + "/api/reels/upload", body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}",
             "Authorization": "Bearer " + tok})
        return json.loads(urllib.request.urlopen(req, timeout=120).read())

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(upload, clips))
    ids = [r["reel_id"] for r in results]
    print(f"uploaded {len(ids)} reels in {time.time()-t0:.1f}s: ids {ids[0]}..{ids[-1]}")

    # poll until all terminal or timeout
    import sqlite3
    deadline = time.time() + 60 * 12
    terminal = {"completed", "failed", "duplicate", "metadata_only"}
    while time.time() < deadline:
        db = sqlite3.connect("data/reelvault.db")
        rows = dict(db.execute(
            f"SELECT id, status FROM reels WHERE id IN ({','.join('?'*len(ids))})",
            ids).fetchall())
        db.close()
        pending = {i: s for i, s in rows.items() if s not in terminal}
        done = len(rows) - len(pending)
        print(f"  [{time.time()-t0:6.1f}s] terminal {done}/{len(ids)}"
              + (f" pending: {pending}" if pending else ""))
        if not pending:
            break
        time.sleep(10)

    # final report
    db = sqlite3.connect("data/reelvault.db")
    db.row_factory = sqlite3.Row
    print("\nid   status      wall_s  err")
    ok = True
    for rid in ids:
        r = db.execute("SELECT status, ingested_at, completed_at,"
                       " error_message FROM reels WHERE id=?", (rid,)).fetchone()
        wall = ""
        if r["completed_at"] and r["ingested_at"]:
            from datetime import datetime
            d = (datetime.fromisoformat(r["completed_at"])
                 - datetime.fromisoformat(r["ingested_at"])).total_seconds()
            wall = f"{d:6.1f}"
        err = (r["error_message"] or "")[:50]
        print(f"{rid:<4} {r['status']:<11} {wall}  {err}")
        if r["status"] not in terminal:
            ok = False
    stuck = db.execute(
        f"SELECT COUNT(*) FROM jobs WHERE reel_id IN ({','.join('?'*len(ids))})"
        " AND status='queued'", ids).fetchone()[0]
    db.close()
    # worker liveness
    health = urllib.request.urlopen(BASE + "/healthz", timeout=10).read()
    print(f"\nhealthz: {health.decode()[:40]}  stuck_queued: {stuck}")
    if stuck:
        ok = False
    print("SOAK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
