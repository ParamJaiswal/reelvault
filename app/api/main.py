"""ReelVault API server + embedded pipeline worker."""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from datetime import date as _date
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.core.config import settings, AUTH_TOKEN
from app.core.logging_setup import setup_logging
from app.db.queue import STAGES, Queue
from app.db.schema import connect, get_db, migrate
from app.ingest.adapters.base import (IngestRequest, parse_shortcode,
                                      route, normalize_instagram_url)
from app.knowledge import assistant as assist
from app.knowledge.search import hybrid_search, keyword_search
from app.pipeline import stages as stg
from app.ai.providers import get_embedder, get_llm

log = logging.getLogger("rv.api")

app = FastAPI(title="ReelVault", docs_url="/api/docs", openapi_url="/api/openapi.json")
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_origins == ["*"] else _cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
queue = Queue()
stg.register_all(queue)


# ------------------------------------------------------------------- auth
def require_auth(request: Request) -> int:
    """Resolve bearer token → user id (bootstrap, JWT, or refresh token)."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else (
        request.query_params.get("token") or "")
    from app.core import auth as A

    ident = A.resolve_request(token)
    if not ident:
        raise HTTPException(401, "Unauthorized")
    with get_db() as db:
        row = db.execute("SELECT id FROM users LIMIT 1").fetchone()
        if not row:
            cur = db.execute(
                "INSERT INTO users(username, display_name, api_key_hash)"
                " VALUES ('owner','Param','')")
            uid = cur.lastrowid
            db.execute(
                "INSERT INTO ingestion_sources(user_id, kind, label)"
                " VALUES (?, 'share_target', 'PWA share target'),"
                "(?, 'url', 'Paste URL'), (?, 'watch_folder', 'Watch folder')",
                (uid, uid, uid))
        else:
            uid = row["id"]
    return ident["user_id"]


def require_role(request: Request, minimum: str) -> dict:
    """Full identity incl. role; enforces role rank."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else (
        request.query_params.get("token") or "")
    from app.core import auth as A

    ident = A.resolve_request(token)
    rank = {"viewer": 0, "admin": 1, "owner": 2}
    if not ident or rank.get(ident["role"], -1) < rank[minimum]:
        raise HTTPException(403, "Insufficient role")
    return ident


def current_identity(request: Request) -> dict | None:
    """Non-raising identity lookup for optional-auth endpoints."""
    try:
        return require_role(request, "viewer")
    except HTTPException:
        return None


# --------------------------------------------------------------- models
class IngestBody(BaseModel):
    url: str | None = None
    caption: str = ""
    author_hint: str = ""


class ShareBody(BaseModel):
    title: str = ""
    text: str = ""
    url: str = ""


class AskBody(BaseModel):
    question: str


class SearchBody(BaseModel):
    query: str
    category: str | None = None
    min_confidence: float | None = None


class FactPatch(BaseModel):
    value: str | None = None
    mark_incorrect: bool = False


# ------------------------------------------------------------ ingestion
def create_reel_from_request(uid: int, req: IngestRequest) -> dict:
    adapter, resolved = route(req)
    shortcode = resolved.get("shortcode")

    with get_db() as db:
        # duplicate short-circuit at ingest time
        if shortcode:
            dup = db.execute(
                "SELECT id, status FROM reels WHERE shortcode=? AND user_id=?"
                " AND status IN ('completed','processing','queued')",
                (shortcode, uid)).fetchone()
            if dup:
                return {"duplicate": True,
                        "reel_id": dup["id"], "status": dup["status"]}

        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " caption, author_handle, media_path, title)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (uid, req.kind, resolved.get("source_url"), shortcode,
             resolved.get("caption") or "", resolved.get("author_handle") or "",
             resolved.get("media_path"), (req.meta.get("title") or "")[:120]))
        reel_id = cur.lastrowid
        ev_row = None
    queue.enqueue(reel_id)
    log.info("ingested reel=%s via %s", reel_id, adapter.name)
    return {"duplicate": False, "reel_id": reel_id, "status": "queued"}


@app.post("/api/reels")
def ingest_url(body: IngestBody, uid: int = Depends(require_auth)):
    try:
        return create_reel_from_request(
            uid, IngestRequest(user_id=uid, kind="url", url=body.url,
                               caption=body.caption,
                               author_hint=body.author_hint))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/reels/upload")
async def ingest_file(file: UploadFile = File(...),
                      url: str = Form(""), caption: str = Form(""),
                      uid: int = Depends(require_auth)):
    dest = settings.media_dir / "video" / f"upload_{int(time.time())}_{file.filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        return create_reel_from_request(
            uid, IngestRequest(user_id=uid, kind="file", local_path=str(dest),
                               url=url or None, caption=caption))
    except ValueError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, str(e))


@app.get("/share-target")
@app.post("/share-target")
async def share_target(request: Request):
    """OS share-sheet landing. Token arrives via the manifest action URL.
    Processes immediately, then redirects to a friendly landing page."""
    from fastapi.responses import RedirectResponse

    from urllib.parse import quote

    request.query_params.get("token")
    try:
        uid = require_auth(request)
    except HTTPException:
        return RedirectResponse("/?shr=err&msg=bad%20token#shared=1", 303)
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        body = ShareBody(**(await request.json()))
    elif "form" in ctype or "multipart" in ctype:
        form = await request.form()
        body = ShareBody(title=form.get("title", ""),
                         text=form.get("text", ""), url=form.get("url", ""))
    else:  # GET-style share target: ?title=&text=&url=
        body = ShareBody(title=request.query_params.get("title", ""),
                         text=request.query_params.get("text", ""),
                         url=request.query_params.get("url", ""))
    try:
        res = create_reel_from_request(
            uid, IngestRequest(user_id=uid, kind="share_target",
                               url=body.url or None,
                               caption=f"{body.title} {body.text}".strip()))
        dest = (f"/?shr=dup&rid={res['reel_id']}#shared=1" if res["duplicate"]
                else f"/?shr=ok&rid={res['reel_id']}#shared=1")
    except ValueError as e:
        dest = f"/?shr=err&msg={quote(str(e)[:100])}#shared=1"
    return RedirectResponse(dest, 303)


WATCH_FOLDER = settings.media_dir.parent / "watch"


@app.post("/api/watch-folder/scan")
def scan_watch_folder(uid: int = Depends(require_auth)):
    WATCH_FOLDER.mkdir(parents=True, exist_ok=True)
    added = []
    for f in sorted(WATCH_FOLDER.glob("*")):
        if f.suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
            continue
        marker = WATCH_FOLDER / f".done_{f.name}"
        if marker.exists():
            continue
        res = create_reel_from_request(
            uid, IngestRequest(user_id=uid, kind="watch_folder",
                               local_path=str(f)))
        marker.write_text(json.dumps(res))
        added.append({"file": f.name, **res})
    return {"added": added}


# ------------------------------------------------------------- reading
@app.get("/api/bootstrap")
def bootstrap(response: JSONResponse):
    """First-visit handshake: hands the local UI its bearer token."""
    return {"token": AUTH_TOKEN, "app": "ReelVault"}


@app.get("/media/thumb/{reel_id}")
def media_thumb(reel_id: int, token: str = "", uid_ok: int = Depends(require_auth)):
    # require_auth above validates the token (query param also accepted)
    with get_db() as db:
        r = db.execute("SELECT thumb_path FROM reels WHERE id=? AND user_id=?",
                       (reel_id, uid_ok)).fetchone()
    if not r or not r["thumb_path"] or not Path(r["thumb_path"]).exists():
        raise HTTPException(404)
    return FileResponse(r["thumb_path"])


@app.get("/media/video/{reel_id}")
def media_video(reel_id: int, uid_ok: int = Depends(require_auth)):
    with get_db() as db:
        r = db.execute("SELECT media_path FROM reels WHERE id=? AND user_id=?",
                       (reel_id, uid_ok)).fetchone()
    if not r or not r["media_path"] or not Path(r["media_path"]).exists():
        raise HTTPException(404)
    return FileResponse(r["media_path"])


def reel_card(r) -> dict:
    d = dict(r)
    d["categories"] = json.loads(d.pop("categories_json") or "[]")
    d["key_takeaways"] = json.loads(d.pop("key_takeaways_json") or "[]")
    d["action_items"] = json.loads(d.pop("action_items_json") or "[]")
    return d


@app.get("/api/reels")
def list_reels(status: str | None = None, category: str | None = None,
               starred: bool | None = None, limit: int = 100,
               offset: int = 0, uid: int = Depends(require_auth)):
    q = ("SELECT * FROM reels WHERE user_id=? AND archived=0")
    args: list = [uid]
    if status:
        q += " AND status=?"; args.append(status)
    if starred is not None:
        q += " AND starred=?"; args.append(int(starred))
    if category:
        q += " AND categories_json LIKE ?"; args.append(f'%"{category}"%')
    q += " ORDER BY priority DESC, ingested_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with get_db() as db:
        rows = [reel_card(r) for r in db.execute(q, args)]
        counts = {r["status"]: r["c"] for r in db.execute(
            "SELECT status, COUNT(*) c FROM reels WHERE user_id=? GROUP BY status",
            (uid,))}
    return {"reels": rows, "counts": counts}


@app.get("/api/reels/{reel_id}")
def reel_detail(reel_id: int, uid: int = Depends(require_auth)):
    with get_db() as db:
        r = db.execute("SELECT * FROM reels WHERE id=? AND user_id=?",
                       (reel_id, uid)).fetchone()
        if not r:
            raise HTTPException(404, "Reel not found")
        card = reel_card(r)
        card["transcript"] = [dict(x) for x in db.execute(
            "SELECT start_s, end_s, text, avg_logprob FROM transcript_segments"
            " WHERE reel_id=? ORDER BY start_s", (reel_id,))]
        card["ocr"] = [dict(x) for x in db.execute(
            "SELECT t_s, text, conf FROM ocr_results WHERE reel_id=?"
            " ORDER BY t_s", (reel_id,))]
        card["facts"] = [dict(x) for x in db.execute(
            "SELECT * FROM facts WHERE reel_id=? ORDER BY confidence DESC,"
            " field", (reel_id,))]
        card["entities"] = [dict(x) for x in db.execute(
            "SELECT e.display_name name, e.kind, re.evidence_quote FROM"
            " reel_entities re JOIN entities e ON e.id=re.entity_id"
            " WHERE re.reel_id=?", (reel_id,))]
        card["events"] = [dict(x) for x in db.execute(
            "SELECT stage, level, message, ts FROM processing_events"
            " WHERE reel_id=? ORDER BY id DESC LIMIT 40", (reel_id,))]
        jobs = [dict(x) for x in db.execute(
            "SELECT stage, status, attempts, last_error FROM jobs"
            " WHERE reel_id=?", (reel_id,))]
        card["jobs"] = jobs
    return card


@app.delete("/api/reels/{reel_id}")
def delete_reel(reel_id: int, purge_media: bool = True,
                uid: int = Depends(require_auth)):
    with get_db() as db:
        r = db.execute("SELECT * FROM reels WHERE id=? AND user_id=?",
                       (reel_id, uid)).fetchone()
        if not r:
            raise HTTPException(404, "Reel not found")
        media = r["media_path"]
        frames_dir = settings.media_dir / "frames" / str(reel_id)
        thumb = r["thumb_path"]
        db.execute("DELETE FROM embeddings WHERE reel_id=?", (reel_id,))
        db.execute("DELETE FROM reels WHERE id=?", (reel_id,))
    if purge_media:
        if media:
            Path(media).unlink(missing_ok=True)
        if thumb:
            Path(thumb).unlink(missing_ok=True)
        shutil.rmtree(frames_dir, ignore_errors=True)
    return {"deleted": reel_id, "media_purged": purge_media}


class ReelPatch(BaseModel):
    starred: bool | None = None
    archived: bool | None = None
    title: str | None = None


@app.patch("/api/reels/{reel_id}")
def patch_reel(reel_id: int, body: ReelPatch,
               uid: int = Depends(require_auth)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(422, "nothing to update")
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_db() as db:
        db.execute(f"UPDATE reels SET {sets} WHERE id=? AND user_id=?",
                   (*fields.values(), reel_id, uid))
    return {"updated": reel_id}


@app.patch("/api/facts/{fact_id}")
def correct_fact(fact_id: int, body: FactPatch,
                 uid: int = Depends(require_auth)):
    """Human feedback loop: correction is stored alongside the AI value so we
    build an evaluation dataset over time."""
    with get_db() as db:
        f = db.execute(
            "SELECT f.* FROM facts f JOIN reels r ON r.id=f.reel_id"
            " WHERE f.id=? AND r.user_id=?", (fact_id, uid)).fetchone()
        if not f:
            raise HTTPException(404, "Fact not found")
        if body.mark_incorrect:
            db.execute(
                "UPDATE facts SET value='[marked incorrect]', user_corrected=1,"
                " updated_at=datetime('now') WHERE id=?", (fact_id,))
        else:
            db.execute(
                "UPDATE facts SET ai_value=CASE WHEN ai_value IS NULL THEN value"
                " ELSE ai_value END, value=?, user_corrected=1,"
                " updated_at=datetime('now') WHERE id=?", (body.value, fact_id))
    return {"corrected": fact_id}


# ------------------------------------------------------- dashboard etc.
@app.get("/api/dashboard")
def dashboard(uid: int = Depends(require_auth)):
    with get_db() as db:
        counts = {r["status"]: r["c"] for r in db.execute(
            "SELECT status, COUNT(*) c FROM reels WHERE user_id=? GROUP BY status",
            (uid,)).fetchall()}
        week = db.execute(
            "SELECT COUNT(*) c FROM reels WHERE user_id=? AND ingested_at >="
            " datetime('now','-7 days')", (uid,)).fetchone()["c"]
        cats = [{"category": json.loads(r["categories_json"])[0] if
                 json.loads(r["categories_json"]) else "Other", "n": 1}
                for r in db.execute(
                    "SELECT categories_json FROM reels WHERE user_id=?"
                    " AND status='completed'", (uid,))]
        agg: dict[str, int] = {}
        for c in cats:
            agg[c["category"]] = agg.get(c["category"], 0) + 1
        recent = [reel_card(r) for r in db.execute(
            "SELECT * FROM reels WHERE user_id=? AND archived=0"
            " ORDER BY ingested_at DESC LIMIT 5", (uid,))]
        radar = [reel_card(r) for r in db.execute(
            "SELECT * FROM reels WHERE user_id=? AND status IN"
           " ('completed','failed') AND priority>=30"
            " ORDER BY priority DESC, ingested_at DESC LIMIT 8", (uid,))]
        unread = db.execute(
            "SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read=0",
            (uid,)).fetchone()["c"]
        top_entities = [dict(r) for r in db.execute(
            "SELECT e.display_name name, e.kind, COUNT(re.reel_id) n FROM"
            " reel_entities re JOIN entities e ON e.id=re.entity_id"
            " JOIN reels r2 ON r2.id=re.reel_id WHERE r2.user_id=?"
            " GROUP BY e.id ORDER BY n DESC LIMIT 12", (uid,))]
        # upcoming deadlines (deterministic dates computed at extraction time)
        upcoming = [reel_card(r) for r in db.execute(
            "SELECT * FROM reels WHERE user_id=? AND reminder_iso IS NOT NULL"
            " AND status='completed'"
            " ORDER BY reminder_iso LIMIT 8", (uid,))]
    queue_stats = queue.stats()
    today = _date.today()
    deadline_cards = []
    for u in upcoming:
        try:
            dleft = max(0, (_date.fromisoformat(u["deadline_iso"][:10])
                            - today).days)
        except Exception:
            dleft = 0
        deadline_cards.append({"id": u["id"], "title": u["title"],
                               "deadline_iso": u["deadline_iso"],
                               "reminder_iso": u["reminder_iso"],
                               "days_left": dleft})
    return {"counts": counts, "new_this_week": week,
            "categories": sorted(agg.items(), key=lambda kv: -kv[1]),
            "recent": recent, "radar": radar, "queue": queue_stats,
            "unread_notifications": unread, "top_entities": top_entities,
            "upcoming_deadlines": deadline_cards}


@app.get("/api/radar")
def radar(uid: int = Depends(require_auth)):
    with get_db() as db:
        rows = [reel_card(r) for r in db.execute(
            "SELECT * FROM reels WHERE user_id=? AND priority>0"
            " ORDER BY priority DESC, ingested_at DESC LIMIT 50", (uid,))]
    return {"radar": rows}


@app.post("/api/search")
def search(body: SearchBody, uid: int = Depends(require_auth)):
    if not body.query.strip():
        return {"results": []}
    emb = get_embedder()
    results = hybrid_search(uid, body.query, emb, limit=40,
                            category=body.category,
                            min_confidence=body.min_confidence)
    return {"results": results}


@app.get("/api/entities")
def entities(uid: int = Depends(require_auth)):
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT e.*, COUNT(re.reel_id) reel_count FROM entities e"
            " LEFT JOIN reel_entities re ON re.entity_id=e.id"
            " WHERE e.user_id=? GROUP BY e.id ORDER BY reel_count DESC LIMIT 200",
            (uid,))]
    return {"entities": rows}


@app.post("/api/assistant/ask")
def ask(body: AskBody, uid: int = Depends(require_auth)):
    if not get_llm().available():
        raise HTTPException(503, "LLM server offline — start llama-server first.")
    return assist.answer_question(uid, body.question)


@app.get("/api/notifications")
def notifications(uid: int = Depends(require_auth)):
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC"
            " LIMIT 50", (uid,))]
        db.execute("UPDATE notifications SET read=1 WHERE user_id=?", (uid,))
    return {"notifications": rows}


# --------------------------------------------------------- admin/debug
@app.get("/api/admin/jobs")
def admin_jobs(uid: int = Depends(require_auth)):
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            """
            SELECT j.id, j.reel_id, j.stage, j.status, j.attempts, j.max_attempts,
                   j.last_error, datetime(j.updated_at,'unixepoch') updated,
                   r.current_stage, r.status reel_status, r.confidence
            FROM jobs j JOIN reels r ON r.id=j.reel_id
            WHERE j.status!='done' OR j.attempts>0
            ORDER BY j.updated_at DESC LIMIT 100""")]
        runs = [dict(r) for r in db.execute(
            "SELECT task, backend, model, latency_ms, tokens_in, tokens_out, ok,"
            " error FROM ai_runs ORDER BY id DESC LIMIT 50")]
        totals = dict(db.execute(
            "SELECT COUNT(*) n, SUM(tokens_in) tin, SUM(tokens_out) tout,"
            " AVG(latency_ms) avg_lat FROM ai_runs").fetchone())
        fails = db.execute("SELECT COUNT(*) c FROM jobs WHERE status='dead'"
                           ).fetchone()["c"]
    return {"jobs": rows, "ai_runs": runs, "ai_totals": totals,
            "queue": queue.stats(), "dead_jobs": fails}


@app.post("/api/admin/retry/{reel_id}")
def retry_failed(reel_id: int, uid: int = Depends(require_auth)):
    with get_db() as db:
        r = db.execute("SELECT current_stage, status FROM reels WHERE id=?",
                       (reel_id,)).fetchone()
        if not r:
            raise HTTPException(404, "not found")
    retried = []
    with get_db() as db:
        dead_or_failed = [dict(x)["stage"] for x in db.execute(
            "SELECT stage FROM jobs WHERE reel_id=? AND status IN"
            " ('failed','dead') ORDER BY id", (reel_id,))]
    if dead_or_failed:
        for s in dead_or_failed:
            if queue.retry_stage(reel_id, s):
                retried.append(s)
        with get_db() as db:
            db.execute("UPDATE reels SET status='processing', error_code=NULL,"
                       " error_message=NULL WHERE id=?", (reel_id,))
    if not retried and r["status"] == "failed":
        # rebuild whole chain after failed stage
        remaining = STAGES[STAGES.index(r["current_stage"]):]
        queue.enqueue(reel_id, remaining)
        retried = remaining
    return {"retrying": retried}


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": "1.1.0"}


# ------------------------------------------------- auth / sessions / push
class LoginBody(BaseModel):
    username: str
    password: str
    name: str = ""


class RefreshBody(BaseModel):
    refresh_token: str


class UserBody(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    display_name: str = ""


class PushSubBody(BaseModel):
    endpoint: str
    keys: dict


@app.post("/api/auth/login")
def auth_login(body: LoginBody):
    from app.core import auth as A

    ident = A.authenticate(body.username, body.password)
    if not ident:
        raise HTTPException(401, "Invalid credentials")
    sess = A.create_session(ident["user_id"], ident["role"], body.name)
    return {"user_id": ident["user_id"], "username": ident["username"],
            "role": ident["role"], **sess}


@app.post("/api/auth/refresh")
def auth_refresh(body: RefreshBody):
    from app.core import auth as A

    result = A.rotate_session(body.refresh_token)
    if not result:
        raise HTTPException(401, "Refresh rejected (expired, revoked, or reused)")
    return result


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    from app.core import auth as A

    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    payload = A.decode_access_jwt(token)
    if payload:
        ok = A.revoke_session(payload["sid"], payload["sub"],
                              payload.get("role", "viewer"))
        return {"revoked": ok}
    raise HTTPException(422, "present an access token to log out")


@app.get("/api/auth/sessions")
def auth_sessions(request: Request):
    me = require_role(request, "viewer")
    from app.core import auth as A

    if me["role"] == "owner":
        return {"sessions": A.list_sessions()}
    return {"sessions": A.list_sessions(me["user_id"])}


@app.delete("/api/auth/sessions/{session_id}")
def auth_revoke(session_id: int, request: Request):
    me = require_role(request, "viewer")
    from app.core import auth as A

    return {"revoked": A.revoke_session(session_id, me["user_id"],
                                        me["role"])}


@app.post("/api/admin/users")
def admin_create_user(body: UserBody, request: Request):
    require_role(request, "owner")
    from app.core import auth as A

    user = A.create_user(body.username, body.password, body.role,
                         body.display_name)
    if not user:
        raise HTTPException(
            422, "invalid input (role must be viewer/admin, password >= 8 chars)")
    return user


@app.get("/api/push/public-key")
def push_public_key():
    from app.core.push import vapid_public_key

    return {"publicKey": vapid_public_key()}


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubBody, request: Request):
    me = require_role(request, "viewer")
    from app.core.push import save_subscription

    ua = request.headers.get("user-agent", "")
    ok = save_subscription(me["user_id"], body.endpoint,
                           body.keys.get("p256dh", ""),
                           body.keys.get("auth", ""), ua)
    return {"saved": ok}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: PushSubBody, request: Request):
    require_role(request, "viewer")
    from app.core.push import remove_subscription

    remove_subscription(body.endpoint)
    return {"removed": True}


@app.post("/api/push/test")
def push_test(request: Request):
    me = require_role(request, "viewer")
    from app.core.push import notify_user

    n = notify_user(me["user_id"], "🔔 ReelVault test",
                    "Push notifications are working!", url="/")
    return {"delivered_to_devices": n}


# ------------------------------------------------------------- backups
@app.post("/api/admin/backup")
def backup_now(request: Request):
    require_role(request, "admin")
    from app.core.backups import run_backup

    return run_backup()


@app.post("/api/admin/maintenance/deadline-scan")
def deadline_scan_now(request: Request):
    require_role(request, "admin")
    from app.core.push import scan_deadlines

    return {"reminders_sent": scan_deadlines()}


# ------------------------------------------- mobile-app ingestion API
class IngestUrlBody(BaseModel):
    url: str
    caption: str = ""


@app.post("/ingest/url")
def ingest_url(body: IngestUrlBody, request: Request):
    """Mobile app: submit a reel URL. Returns immediately; processing is
    asynchronous via the queue."""
    me = require_role(request, "viewer")
    try:
        out = create_reel_from_request(
            me["user_id"],
            IngestRequest(user_id=me["user_id"], kind="url", url=body.url,
                          caption=body.caption))
    except ValueError as e:
        raise HTTPException(422, str(e))
    out["detail"] = f"/summaries/{out['reel_id']}"
    return out


@app.post("/ingest/upload")
async def ingest_upload(request: Request):
    """Mobile app: upload a video file (multipart) directly."""
    me = require_role(request, "viewer")
    form = await request.form()
    up = form.get("file")
    if up is None or not getattr(up, "filename", ""):
        raise HTTPException(422, "multipart field 'file' is required")
    dest = settings.media_dir / "video" / f"mob_{me['user_id']}_{int(time.time())}_{Path(up.filename).name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        shutil.copyfileobj(up.file, fh)
    try:
        out = create_reel_from_request(
            me["user_id"],
            IngestRequest(user_id=me["user_id"], kind="file",
                          local_path=str(dest),
                          caption=str(form.get("caption") or "")))
    except ValueError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, str(e))
    out["detail"] = f"/summaries/{out['reel_id']}"
    return out


@app.get("/summaries")
def summaries(request: Request, limit: int = 50, offset: int = 0,
              category: str | None = None):
    """Mobile app list view: compact cards, newest first."""
    uid = require_auth(request)
    q = ("SELECT id, title, summary, status, current_stage, categories_json,"
         " confidence, priority, deadline_iso FROM reels WHERE user_id=?")
    args: list = [uid]
    if category:
        q += " AND categories_json LIKE ?"
        args.append(f'%"{category}"%')
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [max(1, min(limit, 200)), max(0, offset)]
    items = []
    with get_db() as db:
        for r in db.execute(q, args):
            d = dict(r)
            try:
                cats = json.loads(d.pop("categories_json") or "[]")
            except Exception:
                cats = []
            d["categories"] = cats
            items.append(d)
    return {"items": items, "count": len(items)}


@app.get("/summaries/{reel_id}")
def summary_detail(reel_id: int, uid: int = Depends(require_auth)):
    """Compact detail payload for the mobile detail view (WebView target)."""
    card = reel_detail(reel_id, uid)   # reuses ownership check + full load
    keep = ("id", "title", "summary", "key_takeaways", "action_items",
            "categories", "confidence", "priority", "status", "current_stage",
            "error_code", "deadline_iso", "reminder_iso", "facts",
            "transcript", "ocr", "entities", "source_url", "created_at",
            "starred")
    return {k: card[k] for k in keep if k in card}


@app.get("/readyz")
def readyz():
    checks = {"db": False, "llm": False, "embedder": False}
    try:
        with get_db() as db:
            db.execute("SELECT 1")
        checks["db"] = True
    except Exception:
        pass
    checks["llm"] = get_llm().available()
    try:
        get_embedder().embed(["ping"])
        checks["embedder"] = True
    except Exception:
        pass
    return {"checks": checks, "all_ready": all(checks.values())}


# -------------------------------------------------------------- worker
def worker_loop(poll: float = settings.worker_poll_interval_s):
    wid = f"w{threading.get_ident()}"
    idle_backoff = poll
    while True:
        n = queue.run_pending(wid)
        if n == 0:
            time.sleep(idle_backoff)
        else:
            idle_backoff = poll


@app.on_event("startup")
def startup():
    setup_logging()
    from app.core.config import (validate_startup_paths,
                                 warn_if_owner_credentials_stale)

    validate_startup_paths()       # fail fast on unusable paths
    migrate()
    from app.core import auth as A
    from app.core import push as P

    A.ensure_owner_user()          # owner role + first-run credentials file
    warn_if_owner_credentials_stale()
    P.start_scanner()              # hourly deadline reminders
    from app.core import backups as B

    B.start_scheduler()            # daily encrypted backups (if configured)
    from app.core import llama_manager

    llama_manager.start_async()   # spawn llama-server if not already up
    t = threading.Thread(target=worker_loop, daemon=True, name="rv-worker")
    t.start()
    log.info("ReelVault started; worker live; token=%s...", AUTH_TOKEN[:6])


@app.on_event("shutdown")
def shutdown():
    from app.core import llama_manager

    llama_manager.shutdown()


# --------------------------------------------------------------- static
STATIC = Path(__file__).resolve().parent.parent / "static"


@app.get("/manifest.webmanifest")
def manifest():
    import json as _json

    body = {
        "name": "ReelVault — Reels as Knowledge",
        "short_name": "ReelVault",
        "description": "Turn saved Instagram Reels into a searchable personal knowledge base.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0b0e14",
        "theme_color": "#0b0e14",
        "share_target": {
            "action": f"/share-target?token={AUTH_TOKEN}",
            "method": "GET",
            "params": {"title": "title", "text": "text", "url": "url"},
        },
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192",
             "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JSONResponse(body, media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/{static_file:path}")
def static_files(static_file: str):
    p = (STATIC / static_file).resolve()
    if p.is_file() and str(p).startswith(str(STATIC)):  # no traversal
        return FileResponse(p)
    raise HTTPException(404)


def main():
    setup_logging()
    migrate()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
